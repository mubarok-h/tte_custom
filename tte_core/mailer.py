from __future__ import annotations

import html
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import Document, EmailLog, EmailSettings, LetterRequest, utcnow
from .security import decrypt_secret, encrypt_secret


class MailError(Exception):
    """Kegagalan konfigurasi atau pengiriman email."""


@dataclass
class SmtpConfig:
    enabled: bool
    host: str
    port: int
    username: str
    password: str
    from_name: str
    from_email: str
    use_ssl: bool
    timeout: int
    on_approve: bool
    on_generate: bool
    on_signed: bool
    institution: str
    public_base_url: str

    @property
    def ready(self) -> bool:
        return bool(self.enabled and self.username and self.password)

    @property
    def sender(self) -> str:
        return self.from_email or self.username


def _config_from_settings(settings) -> SmtpConfig:
    return SmtpConfig(
        enabled=settings.email_enabled,
        host=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_user or "",
        password=settings.smtp_password or "",
        from_name=settings.smtp_from_name,
        from_email=settings.smtp_from_email or "",
        use_ssl=settings.smtp_use_ssl,
        timeout=settings.smtp_timeout,
        on_approve=settings.email_on_approve,
        on_generate=settings.email_on_generate,
        on_signed=settings.email_on_signed,
        institution=settings.institution,
        public_base_url=settings.public_base_url,
    )


def get_email_config(db: Session) -> SmtpConfig:
    """Ambil konfigurasi SMTP dari database; buat dari .env bila belum ada."""
    settings = get_settings()
    row = db.execute(select(EmailSettings).limit(1)).scalar_one_or_none()
    if row is None:
        defaults = _config_from_settings(settings)
        row = EmailSettings(
            enabled=defaults.enabled,
            host=defaults.host,
            port=defaults.port,
            username=defaults.username or None,
            from_name=defaults.from_name,
            from_email=defaults.from_email or None,
            use_ssl=defaults.use_ssl,
            timeout=defaults.timeout,
            on_approve=defaults.on_approve,
            on_generate=defaults.on_generate,
            on_signed=defaults.on_signed,
        )
        if defaults.password:
            row.password_enc = encrypt_secret(
                defaults.password, settings.token_secret
            )
        db.add(row)
        db.commit()

    return SmtpConfig(
        enabled=bool(row.enabled),
        host=row.host or "smtp.gmail.com",
        port=int(row.port or 587),
        username=row.username or "",
        password=decrypt_secret(row.password_enc, settings.token_secret) or "",
        from_name=row.from_name or settings.institution,
        from_email=row.from_email or "",
        use_ssl=bool(row.use_ssl),
        timeout=int(row.timeout or 20),
        on_approve=bool(row.on_approve),
        on_generate=bool(row.on_generate),
        on_signed=bool(row.on_signed),
        institution=settings.institution,
        public_base_url=settings.public_base_url,
    )


def store_email_password(db: Session, row: EmailSettings, password: str) -> None:
    settings = get_settings()
    row.password_enc = encrypt_secret(password, settings.token_secret)


def send_email(
    config: SmtpConfig,
    *,
    to: str,
    subject: str,
    text: str,
    html_body: str | None = None,
    attachments: list[tuple[str, bytes, str]] | None = None,
) -> None:
    """Kirim satu email lewat SMTP. Melempar MailError bila gagal."""
    if not config.ready:
        raise MailError(
            "SMTP belum aktif. Aktifkan pengiriman dan isi akun serta "
            "kata sandi aplikasi di halaman Email."
        )
    recipient = (to or "").strip()
    if not recipient or "@" not in recipient:
        raise MailError("Alamat email penerima tidak valid.")

    message = EmailMessage()
    message["From"] = formataddr((config.from_name, config.sender))
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(text)
    if html_body:
        message.add_alternative(html_body, subtype="html")
    for filename, data, mime in attachments or []:
        maintype, _, subtype = mime.partition("/")
        message.add_attachment(
            data,
            maintype=maintype or "application",
            subtype=subtype or "octet-stream",
            filename=filename,
        )

    # Port menentukan mode: 465 = SSL langsung, port lain = STARTTLS.
    # Ini mencegah galat WRONG_VERSION_NUMBER saat opsi SSL tidak cocok.
    implicit_ssl = config.port == 465 or (
        config.use_ssl and config.port not in (25, 587, 2525)
    )

    context = ssl.create_default_context()
    try:
        if implicit_ssl:
            server = smtplib.SMTP_SSL(
                config.host,
                config.port,
                timeout=config.timeout,
                context=context,
            )
        else:
            server = smtplib.SMTP(
                config.host,
                config.port,
                timeout=config.timeout,
            )
        with server as client:
            client.ehlo()
            if not implicit_ssl:
                client.starttls(context=context)
                client.ehlo()
            client.login(config.username, config.password)
            client.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        raise MailError(
            "Login SMTP ditolak. Pastikan memakai App Password Gmail "
            "(16 karakter) dan verifikasi 2 langkah sudah aktif."
        ) from exc
    except ssl.SSLError as exc:
        raise MailError(
            "Mode keamanan tidak cocok dengan port. Gunakan STARTTLS untuk "
            "port 587 atau SSL langsung untuk port 465. "
            f"Detail: {exc}"
        ) from exc
    except (TimeoutError, OSError) as exc:
        if "timed out" in str(exc).lower():
            raise MailError(
                "Koneksi ke server SMTP melewati batas waktu. Periksa host, "
                "port, dan koneksi internet."
            ) from exc
        raise MailError(f"Gagal terhubung ke server SMTP: {exc}") from exc
    except smtplib.SMTPException as exc:
        raise MailError(f"Gagal mengirim email: {exc}") from exc


def _read_attachment(
    db: Session, document: Document | None
) -> tuple[str, bytes, str] | None:
    if document is None:
        return None
    from .drive import ensure_document_file

    variant = (
        "signed"
        if document.signed_path or document.drive_signed_id
        else "original"
    )
    path = ensure_document_file(db, document, variant)
    if path is None or not path.exists():
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    stem = Path(document.filename).stem or "surat"
    suffix = "tte" if variant == "signed" else "pdf"
    return f"{stem}-{suffix}.pdf", data, "application/pdf"


def build_letter_message(
    letter: LetterRequest,
    config: SmtpConfig,
    *,
    has_attachment: bool,
    signed: bool,
) -> tuple[str, str, str]:
    status_url = (
        f"{config.public_base_url}/permohonan/status"
        f"?q={letter.request_no}"
    )
    nomor = letter.letter_no or "(belum bernomor)"
    if signed:
        lead = "Surat Anda telah terbit dan ditandatangani secara elektronik."
    elif has_attachment:
        lead = "Surat Anda telah disetujui dan terlampir pada email ini."
    else:
        lead = "Permohonan surat Anda telah disetujui."

    subject = f"{letter.letter_type} — {nomor}"
    lines = [
        f"Yth. {letter.applicant_name},",
        "",
        lead,
        "",
        f"Nomor permohonan : {letter.request_no}",
        f"Jenis surat      : {letter.letter_type}",
        f"Nomor surat      : {nomor}",
        f"Program studi    : {letter.program_name or '-'}",
        "",
        "Pantau status permohonan:",
        status_url,
        "",
        "Email ini dikirim otomatis oleh sistem TTE "
        f"{config.institution}. Jangan membalas email ini.",
    ]
    text = "\n".join(lines)

    rows = [
        ("Nomor permohonan", letter.request_no),
        ("Jenis surat", letter.letter_type),
        ("Nomor surat", nomor),
        ("Program studi", letter.program_name or "-"),
        ("Nama", letter.applicant_name),
    ]
    table = "".join(
        f'<tr><td style="padding:2px 12px 2px 0;color:#5a6a8a">{html.escape(label)}</td>'
        f'<td style="padding:2px 0"><strong>{html.escape(value)}</strong></td></tr>'
        for label, value in rows
    )
    html_body = (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;'
        'color:#1a2233;line-height:1.6">'
        f"<p>Yth. <strong>{html.escape(letter.applicant_name)}</strong>,</p>"
        f"<p>{html.escape(lead)}</p>"
        f'<table style="border-collapse:collapse;margin:12px 0">{table}</table>'
        f'<p><a href="{html.escape(status_url)}" '
        'style="color:#0d6b3f">Pantau status permohonan</a></p>'
        '<p style="font-size:12px;color:#5a6a8a">Email ini dikirim otomatis oleh '
        f"sistem TTE {html.escape(config.institution)}. Jangan membalas email ini."
        "</p></div>"
    )
    return subject, text, html_body


def send_letter_email(
    db: Session,
    letter: LetterRequest,
    config: SmtpConfig,
    *,
    kind: str = "approved",
    document: Document | None = None,
    force: bool = False,
) -> EmailLog | None:
    """Kirim email surat dan catat hasilnya. Tidak pernah melempar exception."""
    if not force and not config.ready:
        return None

    attachment = _read_attachment(db, document)
    signed = bool(document and document.signed_path)
    subject, text, html_body = build_letter_message(
        letter, config, has_attachment=attachment is not None, signed=signed
    )
    log = EmailLog(
        letter_id=letter.id,
        recipient=letter.email,
        subject=subject,
        kind=kind,
        has_attachment=attachment is not None,
    )
    try:
        send_email(
            config,
            to=letter.email,
            subject=subject,
            text=text,
            html_body=html_body,
            attachments=[attachment] if attachment else [],
        )
        log.status = "sent"
    except MailError as exc:
        log.status = "failed"
        log.error = str(exc)[:500]
    log.created_at = utcnow()
    db.add(log)
    db.commit()
    return log
