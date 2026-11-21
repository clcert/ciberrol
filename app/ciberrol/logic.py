"""Reglas del ejercicio: pasos de cada diapositiva, rotación de quién propone,
elegibilidad para votar, payloads para las vistas, estadísticas y export/import."""

import random
import secrets
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, selectinload

from .models import (
    CATEGORIES,
    LEGACY_CATEGORIES,
    LETTERS,
    MAX_ALTERNATIVES,
    PROFILE_KEYS,
    PROFILES,
    NO_PROFILE,
    STATUS_FINISHED,
    STATUS_RUNNING,
    STEP_PROPOSE,
    STEP_PROPOSED,
    STEP_RESULTS,
    STEP_TEXTS,
    STEP_VOTE,
    CONTEXT_CARD,
    Activity,
    Participant,
    Proposal,
    Slide,
    Vote,
    VoteEligibility,
)

# Margen tras el plazo para recibir lo que alcanzó a escribir/votar cada teléfono.
GRACE = timedelta(seconds=2)
# Un participante está "conectado" si consultó el estado en este lapso.
ONLINE_WINDOW = timedelta(seconds=30)

CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
MAX_PROPOSAL_LEN = 400


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ms(dt: datetime | None) -> int | None:
    return int(dt.timestamp() * 1000) if dt else None


def online_cutoff() -> datetime:
    return utcnow() - ONLINE_WINDOW


def clamp(value, lo: int, hi: int, default: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def new_code(db: Session) -> str:
    while True:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(5))
        if db.scalar(select(Activity.id).where(Activity.code == code)) is None:
            return code


# --------------------------------------------------------------------------- diapositivas


def ordered_slides(db: Session, activity_id: int) -> list[Slide]:
    cat_order = case({c: i for i, c in enumerate(CATEGORIES)}, value=Slide.category, else_=99)
    return list(
        db.scalars(
            select(Slide).where(Slide.activity_id == activity_id).order_by(cat_order, Slide.position, Slide.id)
        )
    )


DEFAULT_INDICATOR = 50


def default_indicators(slides: list[Slide], category: str) -> dict[str, int]:
    """Indicadores iniciales de una diapositiva nueva al final de `category`:
    los de la diapositiva anterior en la línea de tiempo, o 50 si es la primera."""
    cut = CATEGORIES.index(category) if category in CATEGORIES else 0
    prev = [s for s in slides if s.category in CATEGORIES and CATEGORIES.index(s.category) <= cut]
    if not prev:
        return {p: DEFAULT_INDICATOR for p in PROFILE_KEYS}
    return {p: prev[-1].indicator_for(p) for p in PROFILE_KEYS}


def lock_activity(db: Session, activity_id: int) -> Activity:
    return db.scalar(
        select(Activity)
        .where(Activity.id == activity_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


# --------------------------------------------------------------------------- temporizador


def set_timer(act: Activity, seconds: int) -> None:
    """Inicia una cuenta regresiva (o la quita si seconds <= 0)."""
    act.paused_ms = None
    act.deadline = utcnow() + timedelta(seconds=seconds) if seconds > 0 else None


def clear_timer(act: Activity) -> None:
    act.deadline = None
    act.paused_ms = None


def pause_timer(act: Activity) -> None:
    if act.deadline is not None:
        act.paused_ms = max(0, int((act.deadline - utcnow()).total_seconds() * 1000))
        act.deadline = None


def resume_timer(act: Activity) -> None:
    if act.paused_ms is not None:
        act.deadline = utcnow() + timedelta(milliseconds=act.paused_ms)
        act.paused_ms = None


def add_time(act: Activity, seconds: int) -> None:
    if act.paused_ms is not None:
        act.paused_ms += seconds * 1000
    else:
        now = utcnow()
        act.deadline = max(act.deadline or now, now) + timedelta(seconds=seconds)


def timer_payload(act: Activity) -> dict:
    return {"deadline": ms(act.deadline), "paused_ms": act.paused_ms}


# --------------------------------------------------------------------------- roles


def next_role(act: Activity) -> str:
    """Siguiente rol de la permutación actual; al acabarse se baraja una nueva."""
    deck = [r for r in (act.role_deck or []) if r in PROFILES]
    if not deck:
        deck = random.sample(PROFILE_KEYS, len(PROFILE_KEYS))
    role = deck.pop(0)
    act.role_deck = deck
    return role


def assign_roles(db: Session, act: Activity, only_missing: bool) -> int:
    """Asigna roles recorriendo permutaciones de los 4 roles (todos, o solo a quienes no tienen)."""
    if not only_missing:
        act.role_deck = []
    n = 0
    for part in db.scalars(
        select(Participant).where(Participant.activity_id == act.id).order_by(Participant.created_at, Participant.id)
    ):
        if only_missing and part.profile in PROFILES:
            continue
        part.profile = next_role(act)
        n += 1
    if not only_missing:
        act.rotation = {}  # la rotación de proponentes depende de los roles
    return n


# --------------------------------------------------------------------------- máquina de pasos


def _is_due(db: Session, act: Activity) -> bool:
    now = utcnow()
    if act.deadline and now > act.deadline + GRACE:
        return True
    sid = act.current_slide_id
    if act.step == STEP_PROPOSE:
        pending = db.scalar(
            select(func.count()).select_from(Proposal).where(Proposal.slide_id == sid, Proposal.submitted.is_(False))
        )
        return pending == 0  # todos enviaron (o no había nadie para proponer)
    if act.step == STEP_VOTE:
        missing = db.scalar(
            select(func.count())
            .select_from(VoteEligibility)
            .outerjoin(
                Vote,
                (Vote.slide_id == VoteEligibility.slide_id) & (Vote.participant_id == VoteEligibility.participant_id),
            )
            .where(VoteEligibility.slide_id == sid, Vote.id.is_(None))
        )
        return missing == 0  # todos votaron
    return False


def advance_if_due(db: Session, act: Activity) -> Activity:
    """Cierra propuestas/votación cuando vence el plazo o ya respondieron todos."""
    if act.status != STATUS_RUNNING or act.step not in (STEP_PROPOSE, STEP_VOTE):
        return act
    if not _is_due(db, act):
        return act
    act = lock_activity(db, act.id)
    if act.step in (STEP_PROPOSE, STEP_VOTE) and _is_due(db, act):
        if act.step == STEP_PROPOSE:
            # lo que no se envió se toma como enviado (lo que alcanzó a escribir)
            db.query(Proposal).filter(Proposal.slide_id == act.current_slide_id).update({"submitted": True})
            act.step = STEP_PROPOSED
        else:
            act.step = STEP_RESULTS
        clear_timer(act)
    db.commit()
    return act


def _pick_from_rotation(state: dict | None, members: list[str], online: set[str], n: int):
    """Elige n personas conectadas siguiendo una permutación aleatoria del rol.

    Cada persona propone una vez antes de que alguien repita; quienes se unen tarde
    se insertan en una posición aleatoria de la cola pendiente.
    """
    state = state or {}
    mset = set(members)
    queue = [p for p in state.get("queue", []) if p in mset]
    used = [p for p in state.get("used", []) if p in mset]
    known = set(queue) | set(used)
    for p in members:
        if p not in known:
            queue.insert(random.randint(0, len(queue)), p)

    picked: list[str] = []

    def take():
        for p in list(queue):
            if len(picked) >= n:
                break
            if p in online:
                picked.append(p)
                queue.remove(p)

    take()
    if len(picked) < n:  # se acabó la vuelta: nueva permutación con quienes ya propusieron
        recycled = [p for p in used if p not in picked]
        random.shuffle(recycled)
        queue += recycled
        used = []
        take()
    used += picked
    return picked, {"queue": queue, "used": used}


def start_propose(db: Session, act: Activity) -> None:
    slide = db.get(Slide, act.current_slide_id)
    db.execute(delete(VoteEligibility).where(VoteEligibility.slide_id == slide.id))
    db.execute(delete(Proposal).where(Proposal.slide_id == slide.id))  # borra también sus votos
    online = set(
        db.scalars(
            select(Participant.id).where(Participant.activity_id == act.id, Participant.last_seen > online_cutoff())
        )
    )
    rotation = dict(act.rotation or {})
    seq = 0
    for profile in PROFILE_KEYS:
        members = list(
            db.scalars(
                select(Participant.id)
                .where(Participant.activity_id == act.id, Participant.profile == profile)
                .order_by(Participant.created_at, Participant.id)
            )
        )
        picked, rotation[profile] = _pick_from_rotation(
            rotation.get(profile), members, online, slide.num_alternatives
        )
        for pid in picked:
            db.add(Proposal(slide_id=slide.id, participant_id=pid, profile=profile, seq=seq))
            seq += 1
    act.rotation = rotation
    act.step = STEP_PROPOSE
    set_timer(act, act.propose_seconds)


def _vote_context(db: Session, slide_id: int):
    """(perfiles con alternativas, ids de quienes propusieron algo)."""
    rows = db.execute(
        select(Proposal.profile, Proposal.participant_id).where(Proposal.slide_id == slide_id, Proposal.text != "")
    ).all()
    return {r[0] for r in rows}, {r[1] for r in rows}


def start_vote(db: Session, act: Activity) -> None:
    slide = db.get(Slide, act.current_slide_id)
    db.execute(delete(Vote).where(Vote.slide_id == slide.id))
    db.execute(delete(VoteEligibility).where(VoteEligibility.slide_id == slide.id))
    db.query(Proposal).filter(Proposal.slide_id == slide.id).update({"chosen": False})
    roles, proposers = _vote_context(db, slide.id)
    for part in db.scalars(
        select(Participant).where(Participant.activity_id == act.id, Participant.last_seen > online_cutoff())
    ):
        if part.profile in roles and part.id not in proposers:
            db.add(VoteEligibility(slide_id=slide.id, participant_id=part.id))
    act.step = STEP_VOTE
    set_timer(act, act.vote_seconds)


def can_vote(db: Session, slide_id: int, part: Participant) -> bool:
    roles, proposers = _vote_context(db, slide_id)
    return part.profile in roles and part.id not in proposers


def ensure_eligible(db: Session, slide_id: int, participant_id: str) -> None:
    db.execute(
        pg_insert(VoteEligibility)
        .values(slide_id=slide_id, participant_id=participant_id)
        .on_conflict_do_nothing()
    )


def played_step(db: Session, slide_id: int) -> str:
    """Paso en que quedó una diapositiva según lo que ya ocurrió en ella."""
    if db.scalar(select(func.count()).select_from(VoteEligibility).where(VoteEligibility.slide_id == slide_id)):
        return STEP_RESULTS
    if db.scalar(select(func.count()).select_from(Proposal).where(Proposal.slide_id == slide_id)):
        return STEP_PROPOSED
    return STEP_TEXTS


def goto_slide(db: Session, act: Activity, slide_id: int) -> None:
    act.current_slide_id = slide_id
    act.step = played_step(db, slide_id)
    if act.step == STEP_TEXTS:
        set_timer(act, act.discuss_seconds)  # discusión del paso 1
    else:
        clear_timer(act)


def reset_slide(db: Session, act: Activity) -> None:
    sid = act.current_slide_id
    db.execute(delete(VoteEligibility).where(VoteEligibility.slide_id == sid))
    db.execute(delete(Proposal).where(Proposal.slide_id == sid))
    act.step = STEP_TEXTS
    set_timer(act, act.discuss_seconds)


def choose_winner(db: Session, act: Activity, proposal_id: int) -> None:
    """Desempate: el admin elige la ganadora entre las alternativas empatadas de un rol."""
    prop = db.get(Proposal, proposal_id)
    if prop is None or prop.slide_id != act.current_slide_id or act.step != STEP_RESULTS:
        raise ValueError("Alternativa inválida")
    alts = [p for p in _proposals_by_profile(db, prop.slide_id).get(prop.profile, []) if p.text.strip()]
    counts = _vote_counts(db, prop.slide_id)
    top = max(counts.get(a.id, 0) for a in alts)
    tied = [a for a in alts if counts.get(a.id, 0) == top]
    if len(tied) < 2 or prop not in tied:
        raise ValueError("Solo se puede elegir entre alternativas empatadas")
    for a in alts:
        a.chosen = a.id == prop.id


# --------------------------------------------------------------------------- payloads


def _proposals_by_profile(db: Session, slide_id: int) -> dict[str, list[Proposal]]:
    out: dict[str, list[Proposal]] = {p: [] for p in PROFILE_KEYS}
    for prop in db.scalars(
        select(Proposal)
        .options(selectinload(Proposal.participant))
        .where(Proposal.slide_id == slide_id)
        .order_by(Proposal.seq)
    ):
        out.setdefault(prop.profile, []).append(prop)
    return out


def _vote_counts(db: Session, slide_id: int) -> dict[int, int]:
    return dict(
        db.execute(
            select(Vote.proposal_id, func.count()).where(Vote.slide_id == slide_id).group_by(Vote.proposal_id)
        ).all()
    )


def _alternatives(plist: list[Proposal], counts: dict | None, authors: bool) -> tuple[list[dict], int]:
    alts = [p for p in plist if p.text.strip()]
    total = sum(counts.get(a.id, 0) for a in alts) if counts is not None else 0
    top = max([counts.get(a.id, 0) for a in alts], default=0) if counts is not None else 0
    n_top = sum(1 for a in alts if counts.get(a.id, 0) == top) if counts is not None else 0
    any_chosen = any(a.chosen for a in alts)
    out = []
    for i, a in enumerate(alts):
        d = {"id": a.id, "letter": LETTERS[i] if i < len(LETTERS) else str(i + 1), "text": a.text}
        if authors:
            d["author"] = a.participant.name
        if counts is not None:
            n = counts.get(a.id, 0)
            tie = n_top >= 2 and n == top
            d.update(
                count=n,
                pct=round(100 * n / total) if total else 0,
                winner=a.chosen if any_chosen else (n > 0 and n == top),
                tie=tie,
                chosen=a.chosen,
            )
        out.append(d)
    return out, total


def _slide_header(slides: list[Slide], slide: Slide) -> dict:
    idx = next((i for i, s in enumerate(slides) if s.id == slide.id), 0)
    return {
        "id": slide.id,
        "category": slide.category,
        "title": slide.title,
        "index": idx + 1,
        "total": len(slides),
        "is_last": idx == len(slides) - 1,
        "num_alternatives": slide.num_alternatives,
    }


def activity_header(act: Activity, join_url: str) -> dict:
    return {
        "id": act.id,
        "name": act.name,
        "code": act.code,
        "status": act.status,
        "show_qr": act.show_qr,
        "show_authors": act.show_authors,
        "split_contexts": act.split_contexts,
        "join_url": join_url,
        "discuss_seconds": act.discuss_seconds,
        "propose_seconds": act.propose_seconds,
        "vote_seconds": act.vote_seconds,
    }


def board_payload(db: Session, act: Activity, join_url: str, admin_live: bool) -> dict:
    """Estado para la vista de presentación (admin_live=False) o de administración (True)."""
    out = {
        "activity": activity_header(act, join_url),
        "step": act.step,
        **timer_payload(act),
        "now": ms(utcnow()),
        "slide": None,
    }
    slide = db.get(Slide, act.current_slide_id) if act.current_slide_id else None
    if slide is None or act.status != STATUS_RUNNING:
        return out
    out.update(slide_board(db, act, slide, act.step, admin_live))
    return out


def preview_payload(db: Session, act: Activity, slide: Slide, join_url: str) -> dict:
    """Vista previa de una diapositiva para el admin, sin cambiar lo que ven los demás."""
    step = act.step if act.current_slide_id == slide.id and act.status == STATUS_RUNNING else played_step(db, slide.id)
    header = {**activity_header(act, join_url), "status": STATUS_RUNNING, "show_qr": False}
    return {"activity": header, "step": step, "deadline": None, "paused_ms": None, "preview": True,
            **slide_board(db, act, slide, step, admin_live=True)}


def slide_board(db: Session, act: Activity, slide: Slide, step: str, admin_live: bool) -> dict:
    out = {
        "slide": _slide_header(ordered_slides(db, act.id), slide),
        "context": {**CONTEXT_CARD, "text": slide.text_general},
    }
    names = admin_live or act.show_authors  # el panel admin siempre ve los nombres
    hide_role_texts = act.split_contexts and not admin_live  # textos de rol solo en los teléfonos
    props = _proposals_by_profile(db, slide.id)
    show_alts = step in (STEP_PROPOSED, STEP_VOTE, STEP_RESULTS) or (admin_live and step == STEP_PROPOSE)
    show_counts = step == STEP_RESULTS  # resultados solo tras cerrar la votación (también para el admin)
    counts = _vote_counts(db, slide.id) if show_counts else None
    cards = []
    for p in PROFILE_KEYS:
        plist = props.get(p, [])
        card = {
            "profile": p,
            **PROFILES[p],
            "text": "" if hide_role_texts else slide.text_for(p),
            "question": "" if hide_role_texts else slide.question_for(p),
            "text_hidden": hide_role_texts,
            "indicator": slide.indicator_for(p),
            "proposers": [
                {"name": x.participant.name if names else None, "submitted": x.submitted, "has_text": bool(x.text.strip())}
                for x in plist
            ]
            if step != STEP_TEXTS
            else [],
            "alternatives": [],
        }
        if show_alts:
            card["alternatives"], total = _alternatives(
                plist, counts, authors=admin_live or (step == STEP_RESULTS and act.show_authors)
            )
            if counts is not None:
                card["total_votes"] = total
        cards.append(card)
    out["cards"] = cards
    return out


def progress_payload(db: Session, act: Activity) -> dict:
    cutoff = online_cutoff()
    rows = db.execute(
        select(Participant.profile, func.count(), func.count().filter(Participant.last_seen > cutoff))
        .where(Participant.activity_id == act.id)
        .group_by(Participant.profile)
    ).all()
    joined = {r[0]: r[1] for r in rows}
    online = {r[0]: r[2] for r in rows}
    sid = act.current_slide_id
    proposers, submitted, eligible, voted = {}, {}, {}, {}
    if sid and act.status == STATUS_RUNNING:
        for prof, n, s in db.execute(
            select(Proposal.profile, func.count(), func.count().filter(Proposal.submitted.is_(True)))
            .where(Proposal.slide_id == sid)
            .group_by(Proposal.profile)
        ):
            proposers[prof], submitted[prof] = n, s
        eligible = dict(
            db.execute(
                select(Participant.profile, func.count())
                .join(VoteEligibility, VoteEligibility.participant_id == Participant.id)
                .where(VoteEligibility.slide_id == sid)
                .group_by(Participant.profile)
            ).all()
        )
        voted = dict(
            db.execute(
                select(Vote.profile, func.count()).where(Vote.slide_id == sid).group_by(Vote.profile)
            ).all()
        )
    out = {"profiles": [], "totals": defaultdict(int), "unassigned": joined.get(NO_PROFILE, 0)}
    for p in PROFILE_KEYS:
        row = {
            "profile": p,
            **PROFILES[p],
            "joined": joined.get(p, 0),
            "online": online.get(p, 0),
            "proposers": proposers.get(p, 0),
            "submitted": submitted.get(p, 0),
            "eligible": eligible.get(p, 0),
            "voted": voted.get(p, 0),
        }
        row["pending"] = max(0, row["eligible"] - row["voted"])
        out["profiles"].append(row)
        for k in ("joined", "online", "proposers", "submitted", "eligible", "voted", "pending"):
            out["totals"][k] += row[k]
    out["totals"]["joined"] += out["unassigned"]
    out["totals"]["online"] += online.get(NO_PROFILE, 0)
    return out


def participants_list(db: Session, act: Activity) -> list[dict]:
    cutoff = online_cutoff()
    return [
        {"name": p.name, "profile": p.profile if p.profile in PROFILES else NO_PROFILE, "online": p.last_seen > cutoff}
        for p in db.scalars(
            select(Participant).where(Participant.activity_id == act.id).order_by(Participant.created_at)
        )
    ]


def participant_payload(db: Session, act: Activity, part: Participant) -> dict:
    out = {
        "joined": True,
        "name": part.name,
        "profile": part.profile,
        "profiles": PROFILES,
        "activity": {"name": act.name, "status": act.status},
        "step": act.step,
        **timer_payload(act),
        "now": ms(utcnow()),
        "slide": None,
        "has_history": bool(history_slides(db, act)) if part.profile in PROFILES else False,
    }
    slide = db.get(Slide, act.current_slide_id) if act.current_slide_id else None
    if act.status != STATUS_RUNNING or slide is None or part.profile not in PROFILES:
        return out
    out["slide"] = _slide_header(ordered_slides(db, act.id), slide)
    # con split_contexts el contexto general va solo a la presentación
    out["context"] = None if act.split_contexts else {**CONTEXT_CARD, "text": slide.text_general}
    p = part.profile
    plist = _proposals_by_profile(db, slide.id).get(p, [])
    mine = next((x for x in plist if x.participant_id == part.id), None)
    out["card"] = {"profile": p, **PROFILES[p], "text": slide.text_for(p), "question": slide.question_for(p)}
    out["proposers"] = [x.participant.name for x in plist] if act.show_authors else []
    out["n_proposers"] = len(plist)
    out["is_proposer"] = mine is not None
    out["my_proposal"] = {"text": mine.text, "submitted": mine.submitted} if mine else None
    out["alternatives"] = []
    out["my_vote"] = None
    out["can_vote"] = False
    if act.step in (STEP_PROPOSED, STEP_VOTE, STEP_RESULTS):
        counts = _vote_counts(db, slide.id) if act.step == STEP_RESULTS else None
        alts, total = _alternatives(plist, counts, authors=act.step == STEP_RESULTS and act.show_authors)
        out["alternatives"] = alts
        if counts is not None:
            out["total_votes"] = total
        my_vote = db.scalar(select(Vote.proposal_id).where(Vote.slide_id == slide.id, Vote.participant_id == part.id))
        out["my_vote"] = next((a["letter"] for a in alts if a["id"] == my_vote), None)
        if act.step == STEP_RESULTS:
            # Resultados de las 4 tarjetas para todos (la propia primero)
            props = _proposals_by_profile(db, slide.id)
            out["all_results"] = []
            for rp in sorted(PROFILE_KEYS, key=lambda k: k != p):
                ralts, rtotal = _alternatives(props.get(rp, []), counts, authors=act.show_authors)
                out["all_results"].append(
                    {"profile": rp, **PROFILES[rp], "alternatives": ralts, "total_votes": rtotal, "proposers": [], "mine": rp == p}
                )
        if act.step == STEP_VOTE and my_vote is None and can_vote(db, slide.id, part):
            ensure_eligible(db, slide.id, part.id)
            db.commit()
            out["can_vote"] = True
    return out


def history_slides(db: Session, act: Activity) -> list[Slide]:
    """Diapositivas ya vistas: las anteriores a la actual y las que tuvieron propuestas.
    La actual se excluye mientras el ejercicio está en curso (ya se ve en vivo)."""
    if act.status not in (STATUS_RUNNING, STATUS_FINISHED) or not act.current_slide_id:
        return []
    slides = ordered_slides(db, act.id)
    cur = next((i for i, s in enumerate(slides) if s.id == act.current_slide_id), -1)
    played = set(db.scalars(select(Proposal.slide_id).where(Proposal.slide_id.in_([s.id for s in slides])).distinct()))
    return [
        s for i, s in enumerate(slides)
        if (i < cur or s.id in played or (i == cur and act.status == STATUS_FINISHED))
        and not (i == cur and act.status == STATUS_RUNNING)
    ]


def history_payload(db: Session, act: Activity, part: Participant) -> list[dict]:
    """Diapositivas anteriores para un participante, con su propuesta, su voto y los resultados."""
    p = part.profile
    if p not in PROFILES:
        return []
    slides = ordered_slides(db, act.id)
    out = []
    for slide in history_slides(db, act):
        step = played_step(db, slide.id)
        props = _proposals_by_profile(db, slide.id)
        counts = _vote_counts(db, slide.id) if step == STEP_RESULTS else None
        mine = next((x for x in props.get(p, []) if x.participant_id == part.id), None)
        my_vote = db.scalar(select(Vote.proposal_id).where(Vote.slide_id == slide.id, Vote.participant_id == part.id))
        own_alts, _ = _alternatives(props.get(p, []), counts, authors=act.show_authors)
        results = []
        for rp in sorted(PROFILE_KEYS, key=lambda k: k != p):
            ralts, rtotal = _alternatives(props.get(rp, []), counts, authors=act.show_authors)
            card = {"profile": rp, **PROFILES[rp], "alternatives": ralts, "proposers": [], "mine": rp == p}
            if counts is not None:
                card["total_votes"] = rtotal
            results.append(card)
        out.append({
            "slide": _slide_header(slides, slide),
            "step": step,
            "context": None if act.split_contexts else {**CONTEXT_CARD, "text": slide.text_general},
            "card": {"profile": p, **PROFILES[p], "text": slide.text_for(p), "question": slide.question_for(p)},
            "my_proposal": mine.text if mine else None,
            "my_vote": next((a["letter"] for a in own_alts if a["id"] == my_vote), None),
            "results": results,
        })
    return out


# --------------------------------------------------------------------------- estadísticas


def participant_stats(db: Session, activity_id: int) -> list[dict]:
    parts = list(
        db.scalars(
            select(Participant).where(Participant.activity_id == activity_id).order_by(func.lower(Participant.name))
        )
    )
    pids = select(Participant.id).where(Participant.activity_id == activity_id)

    suggested = dict(
        db.execute(
            select(Proposal.participant_id, func.count())
            .where(Proposal.participant_id.in_(pids), Proposal.text != "")
            .group_by(Proposal.participant_id)
        ).all()
    )
    not_suggested = dict(
        db.execute(
            select(Proposal.participant_id, func.count())
            .where(Proposal.participant_id.in_(pids), Proposal.text == "")
            .group_by(Proposal.participant_id)
        ).all()
    )
    votes = dict(
        db.execute(
            select(Vote.participant_id, func.count()).where(Vote.participant_id.in_(pids)).group_by(Vote.participant_id)
        ).all()
    )
    eligible = dict(
        db.execute(
            select(VoteEligibility.participant_id, func.count())
            .where(VoteEligibility.participant_id.in_(pids))
            .group_by(VoteEligibility.participant_id)
        ).all()
    )
    # Ganadoras: la(s) propuesta(s) con más votos (>0) de cada rol en cada diapositiva.
    rows = db.execute(
        select(Proposal.slide_id, Proposal.profile, Proposal.participant_id, func.count(Vote.id), Proposal.chosen)
        .outerjoin(Vote, Vote.proposal_id == Proposal.id)
        .where(Proposal.participant_id.in_(pids), Proposal.text != "")
        .group_by(Proposal.id)
    ).all()
    top: dict[tuple, int] = defaultdict(int)
    decided: set[tuple] = set()  # (diapositiva, rol) con ganadora elegida por el admin
    for sid, prof, _pid, n, chosen in rows:
        top[(sid, prof)] = max(top[(sid, prof)], n)
        if chosen:
            decided.add((sid, prof))
    wins: dict[str, int] = defaultdict(int)
    for sid, prof, pid, n, chosen in rows:
        if chosen if (sid, prof) in decided else (n > 0 and n == top[(sid, prof)]):
            wins[pid] += 1

    return [
        {
            "name": p.name,
            "profile": p.profile,
            "profile_name": PROFILES.get(p.profile, {}).get("name", p.profile),
            "suggested": suggested.get(p.id, 0),
            "not_suggested": not_suggested.get(p.id, 0),
            "wins": wins.get(p.id, 0),
            "voted": votes.get(p.id, 0),
            "not_voted": max(0, eligible.get(p.id, 0) - votes.get(p.id, 0)),
        }
        for p in parts
    ]


# --------------------------------------------------------------------------- export / import

EXPORT_FORMAT = "ciberrol-slides"


def export_slides(db: Session, act: Activity) -> dict:
    return {
        "format": EXPORT_FORMAT,
        "version": 1,
        "activity": act.name,
        "slides": [
            {
                "category": s.category,
                "title": s.title,
                "context": s.text_general,
                "texts": {p: s.text_for(p) for p in PROFILE_KEYS},
                "questions": {p: s.question_for(p) for p in PROFILE_KEYS},
                "indicators": {p: s.indicator_for(p) for p in PROFILE_KEYS},
                "num_alternatives": s.num_alternatives,
            }
            for s in ordered_slides(db, act.id)
        ],
    }


def import_slides(db: Session, act: Activity, data: dict, replace: bool) -> int:
    if not isinstance(data, dict) or data.get("format") != EXPORT_FORMAT or not isinstance(data.get("slides"), list):
        raise ValueError("El archivo no es una exportación de diapositivas de Ciberrol")
    if replace:
        act.current_slide_id = None
        db.flush()
        db.execute(delete(Slide).where(Slide.activity_id == act.id))
    next_pos = {
        c: (db.scalar(select(func.max(Slide.position)).where(Slide.activity_id == act.id, Slide.category == c)) or 0) + 1
        for c in CATEGORIES
    }
    existing = ordered_slides(db, act.id)
    last_inds = (
        {p: existing[-1].indicator_for(p) for p in PROFILE_KEYS}
        if existing
        else {p: DEFAULT_INDICATOR for p in PROFILE_KEYS}
    )
    n = 0
    for item in data["slides"]:
        if not isinstance(item, dict):
            continue
        cat = LEGACY_CATEGORIES.get(item.get("category"), item.get("category"))
        cat = cat if cat in CATEGORIES else CATEGORIES[1]
        texts = item.get("texts") if isinstance(item.get("texts"), dict) else {}
        inds = item.get("indicators") if isinstance(item.get("indicators"), dict) else {}
        qs = item.get("questions") if isinstance(item.get("questions"), dict) else {}
        slide = Slide(
            activity_id=act.id,
            category=cat,
            position=next_pos[cat],
            title=str(item.get("title") or "")[:200],
            text_general=str(item.get("context") or "")[:5000],
            num_alternatives=clamp(item.get("num_alternatives"), 1, MAX_ALTERNATIVES, 3),
        )
        for p in PROFILE_KEYS:
            setattr(slide, f"text_{p}", str(texts.get(p) or "")[:5000])
            setattr(slide, f"q_{p}", str(qs.get(p) or "")[:2000])
            setattr(slide, f"ind_{p}", clamp(inds.get(p), 0, 100, last_inds[p]))
            last_inds[p] = getattr(slide, f"ind_{p}")
        next_pos[cat] += 1
        db.add(slide)
        n += 1
    db.commit()
    return n
