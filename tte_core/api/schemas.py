from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SignerIn(BaseModel):
    name: str = Field(min_length=1)
    jabatan: str = Field(min_length=1)
    nip: str = Field(min_length=1)
    email: str | None = None


class SignRequestIn(BaseModel):
    document_id: str
    signer_id: str | None = None
    signer: SignerIn | None = None
    reason: str = "Pengesahan Dokumen Resmi"
    location: str | None = None


class ConsentIn(BaseModel):
    consent: bool
    note: str | None = None


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    short_id: str
    filename: str
    size_bytes: int
    status: str
    source_hash: str
    final_hash: str | None = None
    created_at: datetime
    verify_url: str | None = None


class SignerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    jabatan: str
    nip: str
    email: str | None = None
    cert_serial: str | None = None
    cert_subject: str | None = None


class SignRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    document_id: str
    signer_id: str
    reason: str | None = None
    location: str | None = None
    status: str
    consent_at: datetime | None = None
    created_at: datetime


class SignResult(BaseModel):
    document_id: str
    signature_id: str
    field_name: str
    pades_level: str
    download_url: str
    verify_url: str


class VerifyOut(BaseModel):
    valid: bool
    status: str
    document: dict | None = None
    signatures: list[dict] = Field(default_factory=list)
    error: str | None = None
