from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import Document, DriveFolder, StorageSettings
from .security import decrypt_secret, encrypt_secret
from .storage import original_path, signed_path, write_bytes

SCOPE = "https://www.googleapis.com/auth/drive"
_API = "https://www.googleapis.com/drive/v3"
_UPLOAD_API = "https://www.googleapis.com/upload/drive/v3"


class DriveError(Exception):
    """Kegagalan konfigurasi atau operasi Google Drive."""


@dataclass
class DriveConfig:
    enabled: bool
    folder_id: str
    credentials_json: str
    account_email: str
    mode: str

    @property
    def ready(self) -> bool:
        return bool(self.enabled and self.credentials_json and self.folder_id)


def _config_from_settings(settings) -> DriveConfig:
    return DriveConfig(
        enabled=settings.drive_enabled,
        folder_id=settings.drive_folder_id or "",
        credentials_json=settings.drive_credentials_json or "",
        account_email="",
        mode="mirror",
    )


def get_drive_config(db: Session) -> DriveConfig:
    """Ambil pengaturan Drive dari database; buat dari .env bila belum ada."""
    settings = get_settings()
    row = db.execute(select(StorageSettings).limit(1)).scalar_one_or_none()
    if row is None:
        defaults = _config_from_settings(settings)
        row = StorageSettings(
            enabled=defaults.enabled,
            folder_id=defaults.folder_id or None,
            account_email=defaults.account_email or None,
            mode=defaults.mode,
        )
        if defaults.credentials_json:
            row.credentials_enc = encrypt_secret(
                defaults.credentials_json, settings.token_secret
            )
        db.add(row)
        db.commit()

    return DriveConfig(
        enabled=bool(row.enabled),
        folder_id=row.folder_id or "",
        credentials_json=(
            decrypt_secret(row.credentials_enc, settings.token_secret) or ""
        ),
        account_email=row.account_email or "",
        mode=row.mode or "mirror",
    )


def store_credentials(
    db: Session, row: StorageSettings, credentials_json: str, email: str
) -> None:
    settings = get_settings()
    row.credentials_enc = encrypt_secret(
        credentials_json, settings.token_secret
    )
    row.account_email = email


_token_cache: dict[str, tuple[str, float]] = {}


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _access_token(config: DriveConfig) -> str:
    try:
        info = json.loads(config.credentials_json)
        client_email = info["client_email"]
        private_key = info["private_key"]
        token_uri = info.get("token_uri", "https://oauth2.googleapis.com/token")
    except (ValueError, KeyError) as exc:
        raise DriveError(
            "Kredensial service account tidak valid (JSON tidak lengkap)."
        ) from exc

    cache_key = f"{client_email}:{token_uri}"
    cached = _token_cache.get(cache_key)
    if cached is not None and cached[1] > time.time() + 60:
        return cached[0]

    now = int(time.time())
    header = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = _b64(
        json.dumps(
            {
                "iss": client_email,
                "scope": SCOPE,
                "aud": token_uri,
                "iat": now,
                "exp": now + 3600,
            }
        ).encode()
    )
    signing_input = f"{header}.{claims}".encode("ascii")
    try:
        key = serialization.load_pem_private_key(
            private_key.encode("utf-8"), password=None
        )
        signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    except Exception as exc:  # noqa: BLE001
        raise DriveError(f"Kunci privat service account tidak valid: {exc}") from exc

    assertion = f"{header}.{claims}.{_b64(signature)}"
    try:
        response = httpx.post(
            token_uri,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
            timeout=20,
        )
    except httpx.HTTPError as exc:
        raise DriveError(f"Tidak dapat menghubungi Google: {exc}") from exc

    if response.status_code != 200:
        raise DriveError(
            "Autentikasi Google ditolak. Periksa service account dan izin folder. "
            f"Detail: {response.text[:200]}"
        )
    token = response.json().get("access_token", "")
    if not token:
        raise DriveError("Google tidak mengembalikan access token.")
    _token_cache[cache_key] = (token, now + 3600)
    return token


def _headers(config: DriveConfig) -> dict[str, str]:
    return {"Authorization": f"Bearer {_access_token(config)}"}


def default_folder_id(db: Session) -> str:
    """Folder default: folder bertanda default, lalu folder global."""
    row = db.execute(
        select(DriveFolder)
        .where(DriveFolder.is_default.is_(True))
        .limit(1)
    ).scalar_one_or_none()
    if row is not None and row.folder_id:
        return row.folder_id
    return get_drive_config(db).folder_id


def folder_for_ref(db: Session, ref: str | None) -> str:
    """Terjemahkan referensi folder pada format surat menjadi Folder ID."""
    if ref:
        row = db.get(DriveFolder, ref)
        if row is not None and row.folder_id:
            return row.folder_id
    return default_folder_id(db)


def test_connection(config: DriveConfig) -> str:
    """Pastikan folder dapat diakses. Kembalikan nama folder."""
    if not config.folder_id:
        raise DriveError("Folder ID Drive belum diisi.")
    if not config.credentials_json:
        raise DriveError("Kredensial service account belum diunggah.")
    try:
        response = httpx.get(
            f"{_API}/files/{config.folder_id}",
            headers=_headers(config),
            params={
                "fields": "id,name,mimeType",
                "supportsAllDrives": "true",
            },
            timeout=20,
        )
    except httpx.HTTPError as exc:
        raise DriveError(f"Tidak dapat menghubungi Google Drive: {exc}") from exc

    if response.status_code == 404:
        raise DriveError(
            "Folder tidak ditemukan atau belum dibagikan ke service account."
        )
    if response.status_code != 200:
        raise DriveError(f"Google Drive menolak permintaan: {response.text[:200]}")
    data = response.json()
    if data.get("mimeType") != "application/vnd.google-apps.folder":
        raise DriveError("ID yang diisi bukan sebuah folder Drive.")
    return data.get("name", "(tanpa nama)")


def upload_bytes(
    config: DriveConfig,
    *,
    name: str,
    data: bytes,
    mime: str,
    folder_id: str | None = None,
) -> str | None:
    """Unggah berkas ke folder Drive. Kembalikan file ID."""
    if not config.ready:
        return None
    target_folder = (folder_id or "").strip() or config.folder_id
    if not target_folder:
        return None
    metadata = {"name": name, "parents": [target_folder]}
    try:
        response = httpx.post(
            f"{_UPLOAD_API}/files",
            headers=_headers(config),
            params={"uploadType": "multipart", "fields": "id", "supportsAllDrives": "true"},
            files={
                "metadata": (
                    "metadata.json",
                    json.dumps(metadata).encode("utf-8"),
                    "application/json",
                ),
                "file": (name, data, mime),
            },
            timeout=120,
        )
    except httpx.HTTPError as exc:
        raise DriveError(f"Gagal mengunggah ke Drive: {exc}") from exc

    if response.status_code not in (200, 201):
        raise DriveError(f"Unggah ke Drive ditolak: {response.text[:200]}")
    return response.json().get("id")


def download_bytes(config: DriveConfig, file_id: str) -> bytes | None:
    if not config.ready or not file_id:
        return None
    try:
        response = httpx.get(
            f"{_API}/files/{file_id}",
            headers=_headers(config),
            params={"alt": "media", "supportsAllDrives": "true"},
            timeout=120,
        )
    except httpx.HTTPError as exc:
        raise DriveError(f"Gagal mengunduh dari Drive: {exc}") from exc

    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise DriveError(f"Unduhan dari Drive ditolak: {response.text[:200]}")
    return response.content


def delete_file(config: DriveConfig, file_id: str) -> None:
    if not config.ready or not file_id:
        return
    try:
        httpx.delete(
            f"{_API}/files/{file_id}",
            headers=_headers(config),
            params={"supportsAllDrives": "true"},
            timeout=20,
        )
    except httpx.HTTPError:
        # Penghapusan best-effort: kegagalan tidak menghentikan proses lokal.
        return


def ensure_document_file(db: Session, doc: Document, variant: str = "signed"):
    """Pastikan berkas tersedia lokal; unduh dari Drive bila hilang."""
    settings = get_settings()
    if variant == "signed":
        raw_path, file_id = doc.signed_path, doc.drive_signed_id
        fallback = signed_path(settings, doc.id)
    else:
        raw_path, file_id = doc.original_path, doc.drive_original_id
        fallback = original_path(settings, doc.id)

    path = Path(raw_path) if raw_path else None
    if path is not None and path.exists():
        return path

    config = get_drive_config(db)
    data = download_bytes(config, file_id) if file_id else None
    if data is None:
        return path if path is not None and path.exists() else None

    target = path or fallback
    write_bytes(target, data)
    if variant == "signed":
        doc.signed_path = str(target)
    else:
        doc.original_path = str(target)
    db.commit()
    return target
