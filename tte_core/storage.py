from __future__ import annotations

from pathlib import Path

from .config import Settings


def document_dir(settings: Settings, document_id: str) -> Path:
    path = settings.documents_dir / document_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def original_path(settings: Settings, document_id: str) -> Path:
    return document_dir(settings, document_id) / "original.pdf"


def signed_path(settings: Settings, document_id: str) -> Path:
    return document_dir(settings, document_id) / "signed.pdf"


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def read_bytes(path: Path) -> bytes:
    return path.read_bytes()
