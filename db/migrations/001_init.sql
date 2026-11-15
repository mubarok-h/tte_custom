-- Skema awal mesin TTE STIT Buntet Pesantren.
-- Aplikasi memakai SQLAlchemy create_all; file ini untuk referensi/migrasi manual.

CREATE TABLE IF NOT EXISTS documents (
    id               TEXT PRIMARY KEY,
    short_id         TEXT NOT NULL UNIQUE,
    source_hash      TEXT NOT NULL,
    final_hash       TEXT,
    original_path    TEXT NOT NULL,
    signed_path      TEXT,
    filename         TEXT NOT NULL,
    size_bytes       BIGINT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'uploaded',
    retention_until  TIMESTAMPTZ NOT NULL,
    archived_at      TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_documents_short_id ON documents(short_id);
CREATE INDEX IF NOT EXISTS ix_documents_status ON documents(status);

CREATE TABLE IF NOT EXISTS signers (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    jabatan          TEXT NOT NULL,
    nip              TEXT NOT NULL,
    email            TEXT,
    cert_serial      TEXT,
    cert_subject     TEXT,
    cert_not_after   TIMESTAMPTZ,
    status           TEXT NOT NULL DEFAULT 'active',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_signers_nip ON signers(nip);

CREATE TABLE IF NOT EXISTS sign_requests (
    id               TEXT PRIMARY KEY,
    document_id      TEXT NOT NULL REFERENCES documents(id),
    signer_id        TEXT NOT NULL REFERENCES signers(id),
    reason           TEXT,
    location         TEXT,
    consent_at       TIMESTAMPTZ,
    consent_ip       TEXT,
    status           TEXT NOT NULL DEFAULT 'pending',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_sign_requests_document_id ON sign_requests(document_id);
CREATE INDEX IF NOT EXISTS ix_sign_requests_signer_id ON sign_requests(signer_id);
CREATE INDEX IF NOT EXISTS ix_sign_requests_status ON sign_requests(status);

CREATE TABLE IF NOT EXISTS signatures (
    id               TEXT PRIMARY KEY,
    document_id      TEXT NOT NULL REFERENCES documents(id),
    sign_request_id  TEXT REFERENCES sign_requests(id),
    field_name       TEXT NOT NULL,
    cert_serial      TEXT,
    signed_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    tsa_time         TIMESTAMPTZ,
    tsa_provider     TEXT,
    pades_level      TEXT NOT NULL DEFAULT 'B-B',
    cms_digest       TEXT,
    page             INTEGER NOT NULL DEFAULT 0,
    rect             TEXT
);
CREATE INDEX IF NOT EXISTS ix_signatures_document_id ON signatures(document_id);

CREATE TABLE IF NOT EXISTS verification_tokens (
    id               TEXT PRIMARY KEY,
    document_id      TEXT NOT NULL REFERENCES documents(id),
    token_hash       TEXT NOT NULL,
    expires_at       TIMESTAMPTZ,
    hit_count        BIGINT NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_verification_tokens_document_id ON verification_tokens(document_id);

CREATE TABLE IF NOT EXISTS revocation_cache (
    cert_serial      TEXT PRIMARY KEY,
    status           TEXT NOT NULL,
    checked_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    source           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    id               SERIAL PRIMARY KEY,
    prev_hash        TEXT,
    event_hash       TEXT NOT NULL UNIQUE,
    event_type       TEXT NOT NULL,
    payload          JSONB NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_audit_events_event_type ON audit_events(event_type);
