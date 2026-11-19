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
    _ensure_letter_columns()
    _ensure_letterhead_columns()
    _ensure_letter_style_columns()
    _ensure_email_settings_columns()
    _ensure_document_columns()
    _ensure_letter_template_columns()
    _ensure_user_columns()
    _ensure_site_settings_columns()
    _seed_default_admin()
    _seed_default_master()
    _seed_role_permissions()
    _seed_letter_defaults()
    _migrate_letter_template_meta()


def _migrate_letter_template_meta() -> None:
    """Pindahkan blok Nomor/Hal ke badan HTML format surat (sekali per baris).

    Dulu renderer menyisipkan blok Nomor/Hal otomatis sehingga tidak bisa
    diubah dari editor. Sekarang blok itu menjadi bagian body_html; baris lama
    yang belum punya ditambahi sekali dan ditandai meta_in_body agar tidak
    terulang walaupun admin menghapus blok tersebut.
    """
    from sqlalchemy import or_, select

    from .letter_docs import META_BLOCK_HTML
    from .models import LetterTemplate

    session = get_session_factory()()
    try:
        rows = (
            session.execute(
                select(LetterTemplate).where(
                    or_(
                        LetterTemplate.meta_in_body.is_(False),
                        LetterTemplate.meta_in_body.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            body = (row.body_html or "").strip()
            row.body_html = f"{META_BLOCK_HTML}\n{body}" if body else META_BLOCK_HTML
            row.meta_in_body = True
        if rows:
            session.commit()
    finally:
        session.close()


def _ensure_letter_columns() -> None:
    """Tambahkan kolom format surat pada tabel letter_requests (DB lama)."""
    from sqlalchemy import inspect, text

    engine = get_engine()
    inspector = inspect(engine)
    if "letter_requests" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("letter_requests")}
    statements = []
    if "template_id" not in columns:
        statements.append(
            "ALTER TABLE letter_requests ADD COLUMN template_id TEXT"
        )
    if "signer_id" not in columns:
        statements.append("ALTER TABLE letter_requests ADD COLUMN signer_id TEXT")
    if "applicant_type" not in columns:
        statements.append(
            "ALTER TABLE letter_requests ADD COLUMN applicant_type VARCHAR(16)"
            " DEFAULT 'mahasiswa'"
        )
    if "nidn" not in columns:
        statements.append("ALTER TABLE letter_requests ADD COLUMN nidn VARCHAR(40)")
    if "jabatan" not in columns:
        statements.append("ALTER TABLE letter_requests ADD COLUMN jabatan TEXT")
    if "employment_status" not in columns:
        statements.append(
            "ALTER TABLE letter_requests ADD COLUMN employment_status VARCHAR(32)"
        )
    if "signer_ids" not in columns:
        statements.append("ALTER TABLE letter_requests ADD COLUMN signer_ids JSON")
    if not statements:
        return
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


def _ensure_letterhead_columns() -> None:
    """Tambahkan kolom baru pada tabel letterheads (DB lama)."""
    from sqlalchemy import inspect, text

    engine = get_engine()
    inspector = inspect(engine)
    if "letterheads" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("letterheads")}
    statements = []
    if "line5" not in columns:
        statements.append("ALTER TABLE letterheads ADD COLUMN line5 TEXT")
    if "accent_color" not in columns:
        statements.append(
            "ALTER TABLE letterheads ADD COLUMN accent_color VARCHAR(16)"
            " DEFAULT '#0d6b3f'"
        )
    if not statements:
        return
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


def _ensure_letter_style_columns() -> None:
    """Tambahkan kolom baru pada tabel letter_styles (DB lama)."""
    from sqlalchemy import inspect, text

    engine = get_engine()
    inspector = inspect(engine)
    if "letter_styles" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("letter_styles")}
    if "kop_gap_mm" in columns:
        return
    with engine.begin() as conn:
        conn.execute(
            text("ALTER TABLE letter_styles ADD COLUMN kop_gap_mm FLOAT DEFAULT 4.0")
        )


def _ensure_document_columns() -> None:
    """Tambahkan kolom Google Drive pada tabel documents (DB lama)."""
    from sqlalchemy import inspect, text

    engine = get_engine()
    inspector = inspect(engine)
    if "documents" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("documents")}
    statements = []
    if "drive_original_id" not in columns:
        statements.append("ALTER TABLE documents ADD COLUMN drive_original_id TEXT")
    if "drive_signed_id" not in columns:
        statements.append("ALTER TABLE documents ADD COLUMN drive_signed_id TEXT")
    if "drive_folder_id" not in columns:
        statements.append("ALTER TABLE documents ADD COLUMN drive_folder_id TEXT")
    if not statements:
        return
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


def _ensure_letter_template_columns() -> None:
    """Tambahkan kolom folder Drive pada letter_templates (DB lama)."""
    from sqlalchemy import inspect, text

    engine = get_engine()
    inspector = inspect(engine)
    if "letter_templates" not in inspector.get_table_names():
        return
    columns = {
        col["name"] for col in inspector.get_columns("letter_templates")
    }
    statements = []
    if "drive_folder_ref" not in columns:
        statements.append(
            "ALTER TABLE letter_templates ADD COLUMN drive_folder_ref TEXT"
        )
    if "audience" not in columns:
        statements.append(
            "ALTER TABLE letter_templates ADD COLUMN audience VARCHAR(16)"
            " DEFAULT 'mahasiswa'"
        )
    if "meta_in_body" not in columns:
        statements.append(
            "ALTER TABLE letter_templates ADD COLUMN meta_in_body BOOLEAN"
            " DEFAULT 0"
        )
    if not statements:
        return
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


def _ensure_user_columns() -> None:
    """Tambahkan kolom unit (fakultas/prodi) pada users (DB lama)."""
    from sqlalchemy import inspect, text

    engine = get_engine()
    inspector = inspect(engine)
    if "users" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("users")}
    statements = []
    if "faculty_id" not in columns:
        statements.append("ALTER TABLE users ADD COLUMN faculty_id TEXT")
    if "program_id" not in columns:
        statements.append("ALTER TABLE users ADD COLUMN program_id TEXT")
    if not statements:
        return
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


def _ensure_site_settings_columns() -> None:
    """Tambahkan kolom baru pada tabel site_settings (DB lama)."""
    from sqlalchemy import inspect, text

    engine = get_engine()
    inspector = inspect(engine)
    if "site_settings" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("site_settings")}
    statements = []
    if "site_title" not in columns:
        statements.append("ALTER TABLE site_settings ADD COLUMN site_title TEXT")
    if "brand_name" not in columns:
        statements.append("ALTER TABLE site_settings ADD COLUMN brand_name TEXT")
    if "brand_subtitle" not in columns:
        statements.append(
            "ALTER TABLE site_settings ADD COLUMN brand_subtitle TEXT"
        )
    if not statements:
        return
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


def _ensure_email_settings_columns() -> None:
    """Tambahkan kolom baru pada tabel email_settings (DB lama)."""
    from sqlalchemy import inspect, text

    engine = get_engine()
    inspector = inspect(engine)
    if "email_settings" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("email_settings")}
    statements = []
    if "on_signed" not in columns:
        statements.append(
            "ALTER TABLE email_settings ADD COLUMN on_signed BOOLEAN DEFAULT 1"
        )
    if not statements:
        return
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


def _seed_letter_defaults() -> None:
    """Isi kop surat dan format surat contoh bila belum tersedia."""
    from .letter_docs import seed_defaults

    settings = get_settings()
    session = get_session_factory()()
    try:
        seed_defaults(session, settings)
    finally:
        session.close()


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


def _seed_role_permissions() -> None:
    """Pastikan setiap role non-admin punya baris izin."""
    from sqlalchemy import select

    from .admin_auth import DEFAULT_ROLE_PERMISSIONS, ROLE_LABELS
    from .models import RolePermission

    session = get_session_factory()()
    try:
        existing = {
            row for row in session.execute(select(RolePermission.role)).scalars()
        }
        for role in ROLE_LABELS:
            if role == "admin" or role in existing:
                continue
            session.add(
                RolePermission(
                    role=role,
                    permissions=sorted(
                        DEFAULT_ROLE_PERMISSIONS.get(role, ["dashboard"])
                    ),
                )
            )
        session.commit()
    finally:
        session.close()


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
