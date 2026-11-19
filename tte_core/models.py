from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    short_id: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    source_hash: Mapped[str] = mapped_column(String(64))
    final_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    original_path: Mapped[str] = mapped_column(Text)
    signed_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    drive_original_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    drive_signed_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    drive_folder_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    filename: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(32), default="uploaded", index=True)
    retention_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    sign_requests: Mapped[list["SignRequest"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    signatures: Mapped[list["SignatureRecord"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class SignerProfile(Base):
    __tablename__ = "signers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(Text)
    jabatan: Mapped[str] = mapped_column(Text)
    nip: Mapped[str] = mapped_column(String(32), index=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    cert_serial: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cert_subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    p12_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    p12_password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    cert_not_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class SignRequest(Base):
    __tablename__ = "sign_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    signer_id: Mapped[str] = mapped_column(ForeignKey("signers.id"), index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[str | None] = mapped_column(Text, nullable=True)
    consent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    consent_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    document: Mapped[Document] = relationship(back_populates="sign_requests")
    signer: Mapped[SignerProfile] = relationship()


class SignatureRecord(Base):
    __tablename__ = "signatures"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    sign_request_id: Mapped[str | None] = mapped_column(
        ForeignKey("sign_requests.id"), nullable=True
    )
    field_name: Mapped[str] = mapped_column(String(64))
    cert_serial: Mapped[str | None] = mapped_column(String(64), nullable=True)
    signed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    tsa_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    tsa_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    pades_level: Mapped[str] = mapped_column(String(16), default="B-B")
    cms_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    page: Mapped[int] = mapped_column(default=0)
    rect: Mapped[str | None] = mapped_column(Text, nullable=True)

    document: Mapped[Document] = relationship(back_populates="signatures")


class VerificationToken(Base):
    __tablename__ = "verification_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    hit_count: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class RevocationCache(Base):
    __tablename__ = "revocation_cache"

    cert_serial: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(16))
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    source: Mapped[str] = mapped_column(String(32))


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prev_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_hash: Mapped[str] = mapped_column(String(64), unique=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(String(24), default="operator", index=True)
    faculty_id: Mapped[str | None] = mapped_column(
        ForeignKey("faculties.id"), nullable=True
    )
    program_id: Mapped[str | None] = mapped_column(
        ForeignKey("study_programs.id"), nullable=True
    )
    password_hash: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class RolePermission(Base):
    """Daftar fitur yang boleh dibuka untuk sebuah role."""

    __tablename__ = "role_permissions"

    role: Mapped[str] = mapped_column(String(32), primary_key=True)
    permissions: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class Institution(Base):
    __tablename__ = "institutions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(Text)
    code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    head_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    head_title: Mapped[str | None] = mapped_column(String(64), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    faculties: Mapped[list["Faculty"]] = relationship(
        back_populates="institution", cascade="all, delete-orphan"
    )


class Faculty(Base):
    __tablename__ = "faculties"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    institution_id: Mapped[str] = mapped_column(
        ForeignKey("institutions.id"), index=True
    )
    name: Mapped[str] = mapped_column(Text)
    code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    head_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    head_title: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    institution: Mapped[Institution] = relationship(back_populates="faculties")
    programs: Mapped[list["StudyProgram"]] = relationship(
        back_populates="faculty", cascade="all, delete-orphan"
    )


class StudyProgram(Base):
    __tablename__ = "study_programs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    faculty_id: Mapped[str] = mapped_column(
        ForeignKey("faculties.id"), index=True
    )
    name: Mapped[str] = mapped_column(Text)
    code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    head_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    head_title: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    faculty: Mapped[Faculty] = relationship(back_populates="programs")


class LetterRequest(Base):
    __tablename__ = "letter_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    request_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    letter_no: Mapped[str | None] = mapped_column(
        String(120), unique=True, nullable=True, index=True
    )

    # Identitas pemohon
    applicant_name: Mapped[str] = mapped_column(Text)
    nim: Mapped[str] = mapped_column(String(40), index=True)
    birth_place: Mapped[str | None] = mapped_column(Text, nullable=True)
    birth_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    email: Mapped[str] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Akademik (snapshot agar riwayat tidak berubah saat master diperbarui)
    institution_id: Mapped[str | None] = mapped_column(
        ForeignKey("institutions.id"), nullable=True
    )
    faculty_id: Mapped[str | None] = mapped_column(
        ForeignKey("faculties.id"), nullable=True
    )
    program_id: Mapped[str | None] = mapped_column(
        ForeignKey("study_programs.id"), nullable=True
    )
    institution_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    faculty_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    program_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    semester: Mapped[str | None] = mapped_column(String(16), nullable=True)
    academic_year: Mapped[str | None] = mapped_column(String(24), nullable=True)

    # Isi permohonan
    letter_type: Mapped[str] = mapped_column(String(120))
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    research_title: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(
        String(24), default="submitted", index=True
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    verified_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Format surat yang dipakai & penanda tangan yang dicetak di isi surat
    template_id: Mapped[str | None] = mapped_column(
        ForeignKey("letter_templates.id"), nullable=True, index=True
    )
    signer_id: Mapped[str | None] = mapped_column(
        ForeignKey("signers.id"), nullable=True
    )

    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id"), nullable=True, index=True
    )

    document: Mapped[Document | None] = relationship()


class DriveFolder(Base):
    """Folder Google Drive yang dapat dipakai per format surat."""

    __tablename__ = "drive_folders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(Text)
    folder_id: Mapped[str] = mapped_column(Text)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class StorageSettings(Base):
    """Pengaturan penyimpanan dokumen di Google Drive."""

    __tablename__ = "storage_settings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    folder_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    credentials_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    account_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    mode: Mapped[str] = mapped_column(String(16), default="mirror")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class EmailSettings(Base):
    """Konfigurasi SMTP yang dapat diubah dari panel admin."""

    __tablename__ = "email_settings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    host: Mapped[str] = mapped_column(String(120), default="smtp.gmail.com")
    port: Mapped[int] = mapped_column(Integer, default=587)
    username: Mapped[str | None] = mapped_column(Text, nullable=True)
    password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    from_name: Mapped[str] = mapped_column(Text, default="STIT Buntet Pesantren")
    from_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    use_ssl: Mapped[bool] = mapped_column(Boolean, default=False)
    timeout: Mapped[int] = mapped_column(Integer, default=20)
    on_approve: Mapped[bool] = mapped_column(Boolean, default=True)
    on_generate: Mapped[bool] = mapped_column(Boolean, default=False)
    on_signed: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class EmailLog(Base):
    """Catatan pengiriman email surat (tanpa menyimpan kata sandi)."""

    __tablename__ = "email_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    letter_id: Mapped[str | None] = mapped_column(
        ForeignKey("letter_requests.id"), nullable=True, index=True
    )
    recipient: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(32), default="approved")
    status: Mapped[str] = mapped_column(String(16), default="sent", index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    has_attachment: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class LetterHead(Base):
    """Kop surat global yang dipakai seluruh format surat."""

    __tablename__ = "letterheads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(Text, default="Kop Surat Utama")
    logo_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    line1: Mapped[str | None] = mapped_column(Text, nullable=True)
    line2: Mapped[str | None] = mapped_column(Text, nullable=True)
    line3: Mapped[str | None] = mapped_column(Text, nullable=True)
    line4: Mapped[str | None] = mapped_column(Text, nullable=True)
    line5: Mapped[str | None] = mapped_column(Text, nullable=True)
    accent_color: Mapped[str] = mapped_column(String(16), default="#0d6b3f")
    html_override: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class LetterStyle(Base):
    """Pengaturan tipografi surat (satu baris global)."""

    __tablename__ = "letter_styles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    body_font: Mapped[str] = mapped_column(
        String(80), default="Times New Roman"
    )
    body_size_pt: Mapped[float] = mapped_column(default=12.0)
    line_height: Mapped[float] = mapped_column(default=1.55)
    paragraph_gap_pt: Mapped[float] = mapped_column(default=10.0)
    margin_top_cm: Mapped[float] = mapped_column(default=2.0)
    margin_bottom_cm: Mapped[float] = mapped_column(default=2.2)
    margin_left_cm: Mapped[float] = mapped_column(default=2.6)
    margin_right_cm: Mapped[float] = mapped_column(default=2.4)
    kop_gap_mm: Mapped[float] = mapped_column(default=4.0)
    kop_line1_size_pt: Mapped[float] = mapped_column(default=11.5)
    kop_main_size_pt: Mapped[float] = mapped_column(default=14.0)
    kop_address_size_pt: Mapped[float] = mapped_column(default=8.5)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class LetterTemplate(Base):
    """Format surat yang bisa diedit admin, satu baris per jenis surat."""

    __tablename__ = "letter_templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    letter_type: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    name: Mapped[str] = mapped_column(Text)
    body_html: Mapped[str] = mapped_column(Text, default="")
    signer_id: Mapped[str | None] = mapped_column(
        ForeignKey("signers.id"), nullable=True
    )
    drive_folder_ref: Mapped[str | None] = mapped_column(
        ForeignKey("drive_folders.id"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
