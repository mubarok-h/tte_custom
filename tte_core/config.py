from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TTE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Aplikasi
    app_name: str = "TTE STIT Buntet Pesantren"
    roles: str = "all"
    institution: str = "STIT Buntet Pesantren"
    location: str = "Buntet Pesantren"
    public_base_url: str = "http://localhost:8000"
    verify_base_url: str = "http://localhost:8000"

    # Database & storage
    database_url: str = "sqlite:///./data/tte.db"
    data_dir: Path = Path("./data")

    # Token
    token_secret: str = "dev-secret-change-me"

    # Sertifikat
    signing_p12: Path | None = Path("./data/pki/rektor.p12")
    signing_p12_passphrase: str = "changeit"
    pkcs11_lib: str | None = None
    pkcs11_key_label: str | None = None
    pkcs11_cert_label: str | None = None
    pkcs11_pin: str | None = None
    root_ca_path: Path = Path("./data/pki/root.crt")
    intermediate_ca_path: Path = Path("./data/pki/intermediate.crt")
    crl_path: Path = Path("./data/pki/crl/stit.crl")
    root_crl_path: Path = Path("./data/pki/crl/root.crl")

    # TSA
    tsa_enabled: bool = False
    tsa_required: bool = False
    embed_ltv: bool = False
    tsa_urls: str = "http://timestamp.digicert.com,http://timestamp.sectigo.com"
    tsa_timeout: int = 10
    tsa_roots_dir: Path = Path("./data/pki/tsa")

    # Batas upload
    max_upload_bytes: int = 25 * 1024 * 1024
    max_pages: int = 500

    # Kebijakan
    retention_years: int = 5
    enable_multisign: bool = True

    # Auth sederhana
    require_auth: bool = False
    admin_token: str = "dev-admin-token"
    admin_username: str = "admin"
    admin_session_hours: int = 12

    @field_validator("signing_p12", mode="before")
    @classmethod
    def _empty_p12_is_none(cls, value):
        return None if value in ("", None) else value

    @property
    def role_set(self) -> set[str]:
        return {r.strip().lower() for r in self.roles.split(",") if r.strip()}

    @property
    def tsa_url_list(self) -> list[str]:
        return [u.strip() for u in self.tsa_urls.split(",") if u.strip()]

    @property
    def documents_dir(self) -> Path:
        return self.data_dir / "documents"

    @property
    def pki_dir(self) -> Path:
        return self.data_dir / "pki"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.documents_dir.mkdir(parents=True, exist_ok=True)
        self.pki_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
