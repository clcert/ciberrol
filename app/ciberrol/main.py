import asyncio
import csv
import io
import json
import os
import re
import uuid
from pathlib import Path

import segno
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from . import logic
from .db import get_db, init_db
from .models import (
    CATEGORIES,
    MAX_ALTERNATIVES,
    PROFILE_KEYS,
    PROFILES,
    STATUS_FINISHED,
    STATUS_LOBBY,
    STATUS_RUNNING,
    STEP_PROPOSE,
    STEP_PROPOSED,
    STEP_TEXTS,
    STEP_VOTE,
    Activity,
    AdminUser,
    Participant,
    Proposal,
    Slide,
    Vote,
)
from .security import verify_password

BASE_DIR = Path(__file__).parent
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").strip().rstrip("/")

SESSION_SECRET = init_db()

app = FastAPI(title="Ciberrol", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    session_cookie="ciberrol",
    max_age=60 * 60 * 24 * 14,
    same_site="lax",
    https_only=os.environ.get("COOKIE_SECURE", "false").lower() == "true",
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.globals.update(PROFILES=PROFILES, CATEGORIES=CATEGORIES, MAX_ALTERNATIVES=MAX_ALTERNATIVES)


def join_url(request: Request, act: Activity) -> str:
    base = PUBLIC_URL or str(request.base_url).rstrip("/")
    return f"{base}/{act.code}"


# =========================================================================== público

CODE_RE = re.compile(r"^[A-Za-z0-9]{4,12}$")


def get_activity_by_code(db: Session, code: str) -> Activity:
    act = db.scalar(select(Activity).where(Activity.code == code.upper())) if CODE_RE.match(code) else None
    if act is None:
        raise HTTPException(404, "Actividad no encontrada")
    return act


def session_participant(request: Request, db: Session, act: Activity) -> Participant | None:
    pid = request.session.get("parts", {}).get(act.code)
    part = db.get(Participant, pid) if pid else None
    if part is not None and part.activity_id != act.id:
        return None
    return part


@app.get("/", response_class=HTMLResponse)
def home(request: Request, code: str | None = None):
    if code:
        return RedirectResponse(f"/{code.strip().upper()}", status_code=303)
    return templates.TemplateResponse(request, "home.html", {})


class JoinIn(BaseModel):
    name: str
    profile: str


@app.post("/api/a/{code}/join")
def join(code: str, body: JoinIn, request: Request, db: Session = Depends(get_db)):
    act = get_activity_by_code(db, code)
    if act.status == STATUS_FINISHED:
        raise HTTPException(409, "La actividad ya terminó")
    name = " ".join(body.name.split())[:60]
    if not name:
        raise HTTPException(400, "Escribe tu nombre")
    if body.profile not in PROFILES:
        raise HTTPException(400, "Perfil inválido")
    part = session_participant(request, db, act)
    if part is None:
        part = Participant(id=str(uuid.uuid4()), activity_id=act.id)
        db.add(part)
    part.name, part.profile = name, body.profile
    part.last_seen = func.now()
    db.commit()
    parts = dict(request.session.get("parts", {}))
    parts[act.code] = part.id
    request.session["parts"] = parts
    return {"ok": True}


@app.get("/api/a/{code}/state")
def participant_state(code: str, request: Request, db: Session = Depends(get_db)):
    act = get_activity_by_code(db, code)
    part = session_participant(request, db, act)
    if part is None:
        return {
            "joined": False,
            "profiles": PROFILES,
            "activity": {"name": act.name, "status": act.status},
        }
    db.execute(
        update(Participant)
        .where(Participant.id == part.id, Participant.last_seen < func.now() - text("interval '5 seconds'"))
        .values(last_seen=func.now())
    )
    db.commit()
    act = logic.advance_if_due(db, act)
    return logic.participant_payload(db, act, part)


class ProposalIn(BaseModel):
    text: str
    final: bool = False


@app.post("/api/a/{code}/proposal")
def save_proposal(code: str, body: ProposalIn, request: Request, db: Session = Depends(get_db)):
    act = get_activity_by_code(db, code)
    part = session_participant(request, db, act)
    if part is None:
        raise HTTPException(400, "Primero únete a la actividad")
    act = logic.lock_activity(db, act.id)
    if (
        act.status != STATUS_RUNNING
        or act.step != STEP_PROPOSE
        or (act.deadline and logic.utcnow() > act.deadline + logic.GRACE)
    ):
        db.rollback()
        raise HTTPException(409, "El tiempo para proponer terminó")
    prop = db.scalar(
        select(Proposal).where(Proposal.slide_id == act.current_slide_id, Proposal.participant_id == part.id)
    )
    if prop is None:
        db.rollback()
        raise HTTPException(403, "No te toca proponer en esta diapositiva")
    if prop.submitted:
        db.rollback()
        raise HTTPException(409, "Ya enviaste tu propuesta")
    prop.text = body.text.strip()[: logic.MAX_PROPOSAL_LEN]
    prop.updated_at = func.now()
    if body.final:
        prop.submitted = True
    db.commit()
    return {"ok": True}


class VoteIn(BaseModel):
    proposal_id: int


@app.post("/api/a/{code}/vote")
def vote(code: str, body: VoteIn, request: Request, db: Session = Depends(get_db)):
    act = get_activity_by_code(db, code)
    part = session_participant(request, db, act)
    if part is None:
        raise HTTPException(400, "Primero únete a la actividad")
    if (
        act.status != STATUS_RUNNING
        or act.step != STEP_VOTE
        or (act.deadline and logic.utcnow() > act.deadline + logic.GRACE)
    ):
        raise HTTPException(409, "La votación no está abierta")
    sid = act.current_slide_id
    prop = db.get(Proposal, body.proposal_id)
    if prop is None or prop.slide_id != sid or prop.profile != part.profile or not prop.text.strip():
        raise HTTPException(400, "Alternativa inválida")
    if not logic.can_vote(db, sid, part):
        raise HTTPException(403, "Quienes propusieron no votan en esta diapositiva")
    logic.ensure_eligible(db, sid, part.id)
    inserted = db.execute(
        pg_insert(Vote)
        .values(participant_id=part.id, slide_id=sid, proposal_id=prop.id, profile=part.profile)
        .on_conflict_do_nothing(index_elements=["participant_id", "slide_id"])
        .returning(Vote.id)
    ).scalar()
    db.commit()
    if inserted is None:
        raise HTTPException(409, "Ya votaste en esta diapositiva")
    return {"ok": True}


# =========================================================================== admin: auth


class NotAuthenticated(Exception):
    pass


@app.exception_handler(NotAuthenticated)
def _not_auth(request: Request, exc: NotAuthenticated):
    if request.url.path.startswith("/admin/api/"):
        return JSONResponse({"detail": "No autenticado"}, status_code=401)
    return RedirectResponse("/admin/login", status_code=303)


def require_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    admin_id = request.session.get("admin_id")
    admin = db.get(AdminUser, admin_id) if admin_id else None
    if admin is None:
        raise NotAuthenticated()
    return admin


@app.get("/admin/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse(request, "admin_login.html", {"error": None})


@app.post("/admin/login", response_class=HTMLResponse)
async def login(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    admin = db.scalar(select(AdminUser).where(AdminUser.username == username))
    if admin is None or not verify_password(password, admin.password_hash):
        await asyncio.sleep(1)
        return templates.TemplateResponse(
            request, "admin_login.html", {"error": "Usuario o clave incorrectos"}, status_code=401
        )
    request.session["admin_id"] = admin.id
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/logout")
def logout(request: Request):
    request.session.pop("admin_id", None)
    return RedirectResponse("/admin/login", status_code=303)


# =========================================================================== admin: actividades


def get_activity(db: Session, activity_id: int) -> Activity:
    act = db.get(Activity, activity_id)
    if act is None:
        raise HTTPException(404, "Actividad no encontrada")
    return act


@app.get("/admin", response_class=HTMLResponse)
def admin_home(request: Request, db: Session = Depends(get_db), admin=Depends(require_admin)):
    n_slides = dict(db.execute(select(Slide.activity_id, func.count()).group_by(Slide.activity_id)).all())
    n_parts = dict(
        db.execute(select(Participant.activity_id, func.count()).group_by(Participant.activity_id)).all()
    )
    acts = list(db.scalars(select(Activity).order_by(Activity.created_at.desc())))
    return templates.TemplateResponse(
        request,
        "admin_activities.html",
        {"activities": acts, "n_slides": n_slides, "n_parts": n_parts, "join_url": lambda a: join_url(request, a)},
    )


@app.post("/admin/activities")
def create_activity(name: str = Form(...), db: Session = Depends(get_db), admin=Depends(require_admin)):
    name = " ".join(name.split())[:200] or "Actividad"
    act = Activity(name=name, code=logic.new_code(db), rotation={})
    db.add(act)
    db.commit()
    return RedirectResponse(f"/admin/a/{act.id}", status_code=303)


@app.post("/admin/activities/{activity_id}/delete")
def delete_activity(activity_id: int, db: Session = Depends(get_db), admin=Depends(require_admin)):
    act = get_activity(db, activity_id)
    act.current_slide_id = None
    db.flush()
    db.delete(act)
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.get("/admin/a/{activity_id}", response_class=HTMLResponse)
def activity_dashboard(request: Request, activity_id: int, db: Session = Depends(get_db), admin=Depends(require_admin)):
    act = get_activity(db, activity_id)
    slides = logic.ordered_slides(db, act.id)
    by_cat = {c: [s for s in slides if s.category == c] for c in CATEGORIES}
    return templates.TemplateResponse(
        request,
        "admin_dashboard.html",
        {"act": act, "by_cat": by_cat, "n_slides": len(slides), "join_url": join_url(request, act)},
    )


@app.get("/admin/a/{activity_id}/presentacion", response_class=HTMLResponse)
def presentation(request: Request, activity_id: int, db: Session = Depends(get_db), admin=Depends(require_admin)):
    act = get_activity(db, activity_id)
    return templates.TemplateResponse(request, "presentation.html", {"act": act})


@app.get("/admin/a/{activity_id}/qr.svg")
def activity_qr(request: Request, activity_id: int, db: Session = Depends(get_db), admin=Depends(require_admin)):
    act = get_activity(db, activity_id)
    buf = io.BytesIO()
    segno.make(join_url(request, act), error="m").save(buf, kind="svg", scale=10, border=2, xmldecl=False)
    return Response(buf.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


@app.get("/admin/api/a/{activity_id}/live")
def live(request: Request, activity_id: int, admin_view: bool = False, db: Session = Depends(get_db), admin=Depends(require_admin)):
    act = logic.advance_if_due(db, get_activity(db, activity_id))
    out = logic.board_payload(db, act, join_url(request, act), admin_live=admin_view)
    out["progress"] = logic.progress_payload(db, act)
    out["n_slides"] = db.scalar(select(func.count()).select_from(Slide).where(Slide.activity_id == act.id))
    if admin_view or act.status == STATUS_LOBBY or act.show_qr:
        out["participants"] = logic.participants_list(db, act)
    return out


class ControlIn(BaseModel):
    slide_id: int | None = None


@app.post("/admin/api/a/{activity_id}/control/{action}")
def control(activity_id: int, action: str, body: ControlIn | None = None, db: Session = Depends(get_db), admin=Depends(require_admin)):
    get_activity(db, activity_id)
    act = logic.lock_activity(db, activity_id)
    slides = logic.ordered_slides(db, act.id)
    ids = [s.id for s in slides]

    def fail(msg: str):
        db.rollback()
        raise HTTPException(400, msg)

    if action == "toggle_qr":
        act.show_qr = not act.show_qr
    elif action == "start":
        if not ids:
            fail("La actividad no tiene diapositivas")
        act.status = STATUS_RUNNING
        act.show_qr = False
        logic.goto_slide(db, act, act.current_slide_id if act.current_slide_id in ids else ids[0])
    elif action == "finish":
        act.status = STATUS_FINISHED
        act.deadline = None
        act.finished_at = logic.utcnow()
    elif action == "reopen":
        act.status = STATUS_RUNNING
        if act.current_slide_id not in ids and ids:
            logic.goto_slide(db, act, ids[0])
    elif action == "goto":
        if not body or body.slide_id not in ids:
            fail("Diapositiva inválida")
        act.status = STATUS_RUNNING
        act.show_qr = False
        logic.goto_slide(db, act, body.slide_id)
    elif act.status != STATUS_RUNNING or act.current_slide_id not in ids:
        fail("El ejercicio no está en curso")
    elif action in ("next", "prev"):
        i = ids.index(act.current_slide_id) + (1 if action == "next" else -1)
        if 0 <= i < len(ids):
            logic.goto_slide(db, act, ids[i])
    elif action == "propose":
        if act.step != STEP_TEXTS:
            fail("Las propuestas solo se inician desde el paso 1")
        logic.start_propose(db, act)
    elif action == "vote":
        if act.step != STEP_PROPOSED:
            fail("La votación se inicia cuando terminan las propuestas")
        logic.start_vote(db, act)
    elif action == "end_now":
        if act.step not in (STEP_PROPOSE, STEP_VOTE):
            fail("No hay un paso con tiempo en curso")
        act.deadline = logic.utcnow()  # tras el margen se cierra solo
    elif action == "reset":
        logic.reset_slide(db, act)
    else:
        fail("Acción desconocida")
    db.commit()
    return {"ok": True, "status": act.status}


# =========================================================================== admin: diapositivas


def _slide_form(request: Request, act: Activity, slide: Slide | None, category: str):
    return templates.TemplateResponse(
        request, "admin_slide_form.html", {"act": act, "slide": slide, "default_category": category}
    )


@app.get("/admin/a/{activity_id}/slides/new", response_class=HTMLResponse)
def new_slide_form(request: Request, activity_id: int, category: str = CATEGORIES[0], db: Session = Depends(get_db), admin=Depends(require_admin)):
    act = get_activity(db, activity_id)
    return _slide_form(request, act, None, category if category in CATEGORIES else CATEGORIES[0])


def _get_slide(db: Session, activity_id: int, slide_id: int) -> Slide:
    slide = db.get(Slide, slide_id)
    if slide is None or slide.activity_id != activity_id:
        raise HTTPException(404, "Diapositiva no encontrada")
    return slide


@app.get("/admin/a/{activity_id}/slides/{slide_id}", response_class=HTMLResponse)
def edit_slide_form(request: Request, activity_id: int, slide_id: int, db: Session = Depends(get_db), admin=Depends(require_admin)):
    act = get_activity(db, activity_id)
    slide = _get_slide(db, activity_id, slide_id)
    return _slide_form(request, act, slide, slide.category)


async def _save_slide(request: Request, db: Session, act: Activity, slide: Slide | None) -> Slide:
    form = await request.form()
    category = form.get("category", "")
    if category not in CATEGORIES:
        raise HTTPException(400, "Fase inválida")
    if slide is None or slide.category != category:
        max_pos = db.scalar(
            select(func.max(Slide.position)).where(Slide.activity_id == act.id, Slide.category == category)
        )
        new_pos = (max_pos if max_pos is not None else -1) + 1
        if slide is None:
            slide = Slide(activity_id=act.id, category=category, position=new_pos)
            db.add(slide)
        else:
            slide.category, slide.position = category, new_pos
    slide.title = (form.get("title") or "").strip()[:200]
    slide.num_alternatives = logic.clamp(form.get("num_alternatives"), 1, MAX_ALTERNATIVES, 3)
    slide.propose_seconds = logic.clamp(form.get("propose_seconds"), 5, 600, 30)
    slide.vote_seconds = logic.clamp(form.get("vote_seconds"), 5, 600, 30)
    for p in PROFILE_KEYS:
        setattr(slide, f"text_{p}", (form.get(f"text_{p}") or "").strip())
        setattr(slide, f"ind_{p}", logic.clamp(form.get(f"ind_{p}"), 0, 100, 0))
    db.commit()
    return slide


@app.post("/admin/a/{activity_id}/slides")
async def create_slide(request: Request, activity_id: int, db: Session = Depends(get_db), admin=Depends(require_admin)):
    act = get_activity(db, activity_id)
    slide = await _save_slide(request, db, act, None)
    return RedirectResponse(f"/admin/a/{act.id}#slide-{slide.id}", status_code=303)


@app.post("/admin/a/{activity_id}/slides/{slide_id}")
async def update_slide(request: Request, activity_id: int, slide_id: int, db: Session = Depends(get_db), admin=Depends(require_admin)):
    act = get_activity(db, activity_id)
    slide = _get_slide(db, activity_id, slide_id)
    await _save_slide(request, db, act, slide)
    return RedirectResponse(f"/admin/a/{act.id}#slide-{slide.id}", status_code=303)


@app.post("/admin/a/{activity_id}/slides/{slide_id}/delete")
def delete_slide(activity_id: int, slide_id: int, db: Session = Depends(get_db), admin=Depends(require_admin)):
    slide = _get_slide(db, activity_id, slide_id)
    act = get_activity(db, activity_id)
    if act.current_slide_id == slide.id:
        act.current_slide_id = None
        db.flush()
    db.delete(slide)
    db.commit()
    return RedirectResponse(f"/admin/a/{activity_id}", status_code=303)


@app.post("/admin/a/{activity_id}/slides/{slide_id}/move")
def move_slide(activity_id: int, slide_id: int, direction: str = Form(...), db: Session = Depends(get_db), admin=Depends(require_admin)):
    slide = _get_slide(db, activity_id, slide_id)
    siblings = list(
        db.scalars(
            select(Slide)
            .where(Slide.activity_id == activity_id, Slide.category == slide.category)
            .order_by(Slide.position, Slide.id)
        )
    )
    i = siblings.index(slide)
    j = i - 1 if direction == "up" else i + 1
    if 0 <= j < len(siblings):
        siblings[i], siblings[j] = siblings[j], siblings[i]
    for pos, s in enumerate(siblings):
        s.position = pos
    db.commit()
    return RedirectResponse(f"/admin/a/{activity_id}#slide-{slide.id}", status_code=303)


@app.post("/admin/a/{activity_id}/slides/{slide_id}/present")
def present_slide(activity_id: int, slide_id: int, db: Session = Depends(get_db), admin=Depends(require_admin)):
    _get_slide(db, activity_id, slide_id)
    act = logic.lock_activity(db, activity_id)
    if act.status != STATUS_RUNNING:
        act.status = STATUS_RUNNING
        act.show_qr = False
    logic.goto_slide(db, act, slide_id)
    db.commit()
    return RedirectResponse(f"/admin/a/{activity_id}", status_code=303)


@app.get("/admin/a/{activity_id}/export")
def export_slides(activity_id: int, db: Session = Depends(get_db), admin=Depends(require_admin)):
    act = get_activity(db, activity_id)
    data = json.dumps(logic.export_slides(db, act), ensure_ascii=False, indent=2)
    fname = re.sub(r"[^A-Za-z0-9_-]+", "_", act.name).strip("_") or "actividad"
    return Response(
        data,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="diapositivas_{fname}.json"'},
    )


@app.post("/admin/a/{activity_id}/import")
async def import_slides(
    request: Request,
    activity_id: int,
    file: UploadFile = File(...),
    replace: bool = Form(False),
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
):
    act = get_activity(db, activity_id)
    raw = await file.read(5 * 1024 * 1024 + 1)
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(413, "Archivo demasiado grande")
    try:
        n = logic.import_slides(db, act, json.loads(raw.decode("utf-8")), replace)
    except (ValueError, UnicodeDecodeError) as e:
        db.rollback()
        return templates.TemplateResponse(
            request, "admin_message.html", {"act": act, "message": f"No se pudo importar: {e}"}, status_code=400
        )
    return RedirectResponse(f"/admin/a/{activity_id}?importadas={n}", status_code=303)


# =========================================================================== admin: resultados


@app.get("/admin/a/{activity_id}/resultados", response_class=HTMLResponse)
def results_page(request: Request, activity_id: int, db: Session = Depends(get_db), admin=Depends(require_admin)):
    act = get_activity(db, activity_id)
    return templates.TemplateResponse(
        request, "admin_results.html", {"act": act, "rows": logic.participant_stats(db, act.id)}
    )


@app.get("/admin/a/{activity_id}/resultados.csv")
def results_csv(activity_id: int, db: Session = Depends(get_db), admin=Depends(require_admin)):
    act = get_activity(db, activity_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Nombre", "Rol", "Sugirió", "No sugirió", "Su alternativa ganó", "Votó", "No votó"])
    for r in logic.participant_stats(db, act.id):
        w.writerow([r["name"], r["profile_name"], r["suggested"], r["not_suggested"], r["wins"], r["voted"], r["not_voted"]])
    fname = re.sub(r"[^A-Za-z0-9_-]+", "_", act.name).strip("_") or "actividad"
    return Response(
        "﻿" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="resultados_{fname}.csv"'},
    )


@app.get("/healthz")
def healthz():
    return {"ok": True}


# =========================================================================== URL corta (al final)


@app.get("/{code}", response_class=HTMLResponse)
def join_page(request: Request, code: str, db: Session = Depends(get_db)):
    if not CODE_RE.match(code):
        raise HTTPException(404)
    act = db.scalar(select(Activity).where(Activity.code == code.upper()))
    if act is None:
        return templates.TemplateResponse(
            request, "home.html", {"error": f"No existe una actividad con el código {code.upper()}"}, status_code=404
        )
    if code != act.code:
        return RedirectResponse(f"/{act.code}", status_code=303)
    return templates.TemplateResponse(request, "participant.html", {"code": act.code})
