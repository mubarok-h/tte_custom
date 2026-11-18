from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings = get_settings()
        settings.ensure_dirs()
        connect_args = {}
        if settings.database_url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
        _engine = create_engine(
            settings.database_url,
            connect_args=connect_args,
            pool_pre_ping=True,
            future=True,
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(),
            autoflush=False,
            expire_on_commit=False,
        )
    return _session_factory


def get_db() -> Generator[Session, None, None]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def init_db() -> None:
    from . import models  # noqa: F401  (register mappers)

    Base.metadata.create_all(get_engine())
    _ensure_signer_columns()
    _seed_default_admin()
    _seed_default_master()


def _ensure_signer_columns() -> None:
    """Tambahkan kolom baru pada tabel signers untuk database lama."""
    from sqlalchemy import inspect, text

    engine = get_engine()
    inspector = inspect(engine)
    if "signers" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("signers")}
    statements = []
    if "p12_path" not in columns:
        statements.append("ALTER TABLE signers ADD COLUMN p12_path TEXT")
    if "p12_password_enc" not in columns:
        statements.append(
            "ALTER TABLE signers ADD COLUMN p12_password_enc TEXT"
        )
    if not statements:
        return
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


def _seed_default_master() -> None:
    """Isi master data dasar saat database masih kosong."""
    from sqlalchemy import select

    from .models import Faculty, Institution, StudyProgram

    settings = get_settings()
    session = get_session_factory()()
    try:
        existing = session.execute(
            select(Institution).limit(1)
        ).scalar_one_or_none()
        if existing is not None:
            return

        institution = Institution(
            name=settings.institution,
            code="STIT-BP",
            head_title="Ketua",
            address=f"{settings.location}, Cirebon",
        )
        session.add(institution)
        session.flush()

        faculty = Faculty(
            institution_id=institution.id,
            name="Fakultas Tarbiyah",
            code="FT",
            head_title="Dekan",
        )
        session.add(faculty)
        session.flush()

        session.add_all(
            [
                StudyProgram(
                    faculty_id=faculty.id,
                    name="Bimbingan Konseling Pendidikan Islam",
                    code="BKPI",
                    level="S1",
                    head_title="Ketua Program Studi",
                ),
                StudyProgram(
                    faculty_id=faculty.id,
                    name="Manajemen Pendidikan Islam",
                    code="MPI",
                    level="S1",
                    head_title="Ketua Program Studi",
                ),
            ]
        )
        session.commit()
    finally:
        session.close()


def _seed_default_admin() -> None:
    """Buat akun admin awal dari TTE_ADMIN_USERNAME / TTE_ADMIN_TOKEN."""
    from sqlalchemy import select

    from .models import User
    from .security import hash_password

    settings = get_settings()
    if not settings.admin_username.strip() or not settings.admin_token.strip():
        return

    session = get_session_factory()()
    try:
        existing = session.execute(
            select(User).where(User.username == settings.admin_username)
        ).scalar_one_or_none()
        if existing is not None:
            return
        user = User(
            username=settings.admin_username,
            name="Administrator",
            role="admin",
            password_hash=hash_password(settings.admin_token),
        )
        session.add(user)
        session.commit()
    finally:
        session.close()


def reset_state() -> None:
    """Hanya dipakai oleh test untuk membuang engine/session cache."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
