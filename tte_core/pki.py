from __future__ import annotations

from pathlib import Path

from asn1crypto import crl as asn1_crl
from asn1crypto import pem, x509
from pyhanko.sign import signers
from pyhanko_certvalidator import ValidationContext

from .config import Settings


class PkiError(RuntimeError):
    pass


def load_cert(path: Path) -> x509.Certificate:
    if not path.exists():
        raise PkiError(f"Sertifikat tidak ditemukan: {path}")
    data = path.read_bytes()
    if pem.detect(data):
        _, _, der = pem.unarmor(data)
        return x509.Certificate.load(der)
    return x509.Certificate.load(data)


def load_crl(path: Path) -> asn1_crl.CertificateList | None:
    if not path.exists():
        return None
    data = path.read_bytes()
    if pem.detect(data):
        _, _, der = pem.unarmor(data)
        return asn1_crl.CertificateList.load(der)
    return asn1_crl.CertificateList.load(data)


def load_signer(settings: Settings):
    """Muat signer dari HSM (PKCS#11) atau file PKCS#12."""
    if settings.pkcs11_lib:
        try:
            from pyhanko.sign.pkcs11 import PKCS11Signer, open_pkcs11_session
        except ImportError as exc:  # pragma: no cover - butuh hardware
            raise PkiError(
                "PKCS#11 dipilih, tetapi paket python-pkcs11 belum terpasang. "
                "Install dengan: pip install 'python-pkcs11'"
            ) from exc

        intermediate = (
            load_cert(settings.intermediate_ca_path)
            if settings.intermediate_ca_path.exists()
            else None
        )
        session = open_pkcs11_session(
            settings.pkcs11_lib,
            user_pin=settings.pkcs11_pin or None,
        )
        return PKCS11Signer(
            session,
            cert_label=settings.pkcs11_cert_label,
            key_label=settings.pkcs11_key_label,
            ca_chain=[intermediate] if intermediate else None,
        )

    if settings.signing_p12 and settings.signing_p12.exists():
        return signers.SimpleSigner.load_pkcs12(
            str(settings.signing_p12),
            passphrase=settings.signing_p12_passphrase.encode("utf-8"),
        )

    raise PkiError(
        "Tidak ada signer. Set TTE_SIGNING_P12 atau konfigurasi PKCS#11 "
        "(TTE_PKCS11_LIB)."
    )


def load_tsa_roots(settings: Settings) -> list[x509.Certificate]:
    roots: list[x509.Certificate] = []
    if settings.tsa_roots_dir.exists():
        for path in sorted(settings.tsa_roots_dir.glob("*.crt")):
            try:
                roots.append(load_cert(path))
            except PkiError:
                continue
    return roots


def embedding_validation_context(settings: Settings) -> ValidationContext:
    root = load_cert(settings.root_ca_path)
    other = []
    if settings.intermediate_ca_path.exists():
        other.append(load_cert(settings.intermediate_ca_path))
    crls = []
    for path in (settings.crl_path, settings.root_crl_path):
        crl = load_crl(path)
        if crl is not None:
            crls.append(crl)
    return ValidationContext(
        trust_roots=[root, *load_tsa_roots(settings)],
        other_certs=other,
        crls=crls,
        allow_fetching=True,
        revocation_mode="soft-fail",
    )


def validation_context(settings: Settings) -> ValidationContext:
    root = load_cert(settings.root_ca_path)
    other = []
    if settings.intermediate_ca_path.exists():
        other.append(load_cert(settings.intermediate_ca_path))
    crls = []
    for path in (settings.crl_path, settings.root_crl_path):
        crl = load_crl(path)
        if crl is not None:
            crls.append(crl)
    return ValidationContext(
        trust_roots=[root],
        other_certs=other,
        crls=crls,
        allow_fetching=True,
        revocation_mode="hard-fail",
    )


def tsa_validation_context(settings: Settings) -> ValidationContext | None:
    roots = load_tsa_roots(settings)
    if not roots:
        return None
    return ValidationContext(
        trust_roots=roots,
        allow_fetching=True,
        revocation_mode="soft-fail",
    )
