from __future__ import annotations

import os
import secrets
import shutil
import subprocess
from pathlib import Path

from .config import Settings
from .models import SignerProfile
from .pki import PkiError, load_cert
from .security import encrypt_secret

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ISSUE_SCRIPT = _PROJECT_ROOT / "ca" / "scripts" / "issue-leaf.sh"


def signer_cert_dir(settings: Settings, signer_id: str) -> Path:
    return settings.pki_dir / "signers" / signer_id


def issue_signer_certificate(
    signer: SignerProfile, settings: Settings
) -> Path:
    """Terbitkan sertifikat leaf + P12 untuk satu penanda tangan.

    Sertifikat ditandatangani oleh intermediate CA yang tersedia di mesin ini.
    """
    if not _ISSUE_SCRIPT.exists():
        raise PkiError("Skrip penerbitan sertifikat CA tidak ditemukan.")
    if not signer.id:
        raise PkiError("Penanda tangan belum memiliki ID.")

    outdir = signer_cert_dir(settings, signer.id)
    shutil.rmtree(outdir, ignore_errors=True)
    outdir.mkdir(parents=True, exist_ok=True)
    os.chmod(outdir, 0o700)

    password = secrets.token_urlsafe(18)
    env = os.environ.copy()
    env.setdefault("DEV_DIR", str(_PROJECT_ROOT / "ca" / "dev"))
    env.setdefault("PKI_OUT", str(settings.pki_dir))
    env["ORG"] = settings.institution
    env["PKCS12_PASS"] = password
    env["LEAF_DAYS"] = "730"

    result = subprocess.run(
        [
            "bash",
            str(_ISSUE_SCRIPT),
            signer.name,
            signer.nip,
            signer.email or "",
            str(outdir),
        ],
        cwd=str(_PROJECT_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    if result.returncode != 0:
        shutil.rmtree(outdir, ignore_errors=True)
        detail = (result.stderr or result.stdout or "").strip()
        raise PkiError(
            "Gagal menerbitkan sertifikat penanda tangan"
            + (f": {detail[-400:]}" if detail else ".")
        )

    p12_files = sorted(outdir.glob("*.p12"))
    crt_files = sorted(outdir.glob("*.crt"))
    if not p12_files or not crt_files:
        shutil.rmtree(outdir, ignore_errors=True)
        raise PkiError("Sertifikat penanda tangan tidak terbentuk.")

    p12_path = p12_files[0]
    cert = load_cert(crt_files[0])
    signer.p12_path = str(p12_path)
    signer.p12_password_enc = encrypt_secret(password, settings.token_secret)
    signer.cert_serial = str(cert.serial_number)
    signer.cert_subject = str(cert.subject.native)
    signer.cert_not_after = cert["tbs_certificate"]["validity"][
        "not_after"
    ].native
    os.chmod(p12_path, 0o600)
    return p12_path


def delete_signer_certificate(
    signer: SignerProfile, settings: Settings
) -> None:
    """Hapus hanya file sertifikat milik penanda tangan yang dihapus."""
    base = (settings.pki_dir / "signers").resolve()
    target = signer_cert_dir(settings, signer.id).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        return
    shutil.rmtree(target, ignore_errors=True)
