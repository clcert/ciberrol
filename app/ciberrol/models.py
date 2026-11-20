from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# Perfiles / tipos de card. El orden define la grilla 2x2.
PROFILES = {
    "directivo": {"name": "Directivo", "color": "#facc15", "ink": "#1c1917", "emoji": "👔"},            # amarillo
    "comunicacional": {"name": "Comunicacional", "color": "#22c55e", "ink": "#052e16", "emoji": "📢"},  # verde
    "legal": {"name": "Legal", "color": "#dc2626", "ink": "#ffffff", "emoji": "⚖️"},                    # rojo
    "tecnico": {"name": "Técnico", "color": "#2563eb", "ink": "#ffffff", "emoji": "🛠️"},               # azul
}
PROFILE_KEYS = list(PROFILES)

CATEGORIES = ["Contener", "Mitigar", "Recuperar", "Aprendizaje"]
LETTERS = "ABCDEF"
MAX_ALTERNATIVES = len(LETTERS)

# Estado de una actividad
STATUS_LOBBY = "lobby"        # mostrando QR, esperando que se unan
STATUS_RUNNING = "running"
STATUS_FINISHED = "finished"

# Pasos de cada diapositiva
STEP_TEXTS = "texts"          # 1. se muestran los 4 textos
STEP_PROPOSE = "propose"      # 2. personas elegidas proponen (con tiempo)
STEP_PROPOSED = "proposed"    # 2b. propuestas cerradas, esperando que el admin inicie la votación
STEP_VOTE = "vote"            # 3. votación (con tiempo)
STEP_RESULTS = "results"      # 4. resultados


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class AdminUser(Base):
    __tablename__ = "admin_users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Activity(Base):
    __tablename__ = "activities"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str] = mapped_column(String(12), unique=True)
    status: Mapped[str] = mapped_column(String(16), default=STATUS_LOBBY)
    current_slide_id: Mapped[int | None] = mapped_column(
        ForeignKey("slides.id", ondelete="SET NULL", use_alter=True), nullable=True
    )
    step: Mapped[str] = mapped_column(String(16), default=STEP_TEXTS)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    show_qr: Mapped[bool] = mapped_column(Boolean, default=False)
    # Rotación de quién propone, por rol: {perfil: {"queue": [pid...], "used": [pid...]}}
    rotation: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    slides: Mapped[list["Slide"]] = relationship(
        back_populates="activity", cascade="all, delete-orphan", foreign_keys="Slide.activity_id"
    )


class Slide(Base):
    __tablename__ = "slides"
    id: Mapped[int] = mapped_column(primary_key=True)
    activity_id: Mapped[int] = mapped_column(ForeignKey("activities.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(32))
    position: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String(200), default="")
    text_directivo: Mapped[str] = mapped_column(Text, default="")
    text_comunicacional: Mapped[str] = mapped_column(Text, default="")
    text_legal: Mapped[str] = mapped_column(Text, default="")
    text_tecnico: Mapped[str] = mapped_column(Text, default="")
    ind_directivo: Mapped[int] = mapped_column(Integer, default=0)
    ind_comunicacional: Mapped[int] = mapped_column(Integer, default=0)
    ind_legal: Mapped[int] = mapped_column(Integer, default=0)
    ind_tecnico: Mapped[int] = mapped_column(Integer, default=0)
    num_alternatives: Mapped[int] = mapped_column(Integer, default=3)
    propose_seconds: Mapped[int] = mapped_column(Integer, default=30)
    vote_seconds: Mapped[int] = mapped_column(Integer, default=30)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    activity: Mapped[Activity] = relationship(back_populates="slides", foreign_keys=[activity_id])

    def text_for(self, profile: str) -> str:
        return getattr(self, f"text_{profile}")

    def indicator_for(self, profile: str) -> int:
        return getattr(self, f"ind_{profile}")


class Participant(Base):
    __tablename__ = "participants"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # uuid4
    activity_id: Mapped[int] = mapped_column(ForeignKey("activities.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    profile: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Proposal(Base):
    """Asignación de una persona para proponer en una diapositiva, y lo que escribió."""

    __tablename__ = "proposals"
    __table_args__ = (UniqueConstraint("slide_id", "participant_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    slide_id: Mapped[int] = mapped_column(ForeignKey("slides.id", ondelete="CASCADE"), index=True)
    participant_id: Mapped[str] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"))
    profile: Mapped[str] = mapped_column(String(32))
    seq: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text, default="")
    submitted: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    participant: Mapped[Participant] = relationship()


class VoteEligibility(Base):
    """Quién debía votar en una diapositiva (para contar 'no votó')."""

    __tablename__ = "vote_eligibility"
    slide_id: Mapped[int] = mapped_column(ForeignKey("slides.id", ondelete="CASCADE"), primary_key=True)
    participant_id: Mapped[str] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"), primary_key=True)


class Vote(Base):
    __tablename__ = "votes"
    __table_args__ = (UniqueConstraint("participant_id", "slide_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    participant_id: Mapped[str] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"))
    slide_id: Mapped[int] = mapped_column(ForeignKey("slides.id", ondelete="CASCADE"), index=True)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("proposals.id", ondelete="CASCADE"))
    profile: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
