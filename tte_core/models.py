from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Integer, String, Text
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
