from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuditEvent


def canonical_json(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def append_event(db: Session, event_type: str, payload: dict) -> AuditEvent:
    last = db.execute(
        select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1)
    ).scalar_one_or_none()
    prev_hash = last.event_hash if last else ""
    body = canonical_json(payload)
    event_hash = hashlib.sha256((prev_hash + body).encode("utf-8")).hexdigest()
    event = AuditEvent(
        prev_hash=prev_hash or None,
        event_hash=event_hash,
        event_type=event_type,
        payload=payload,
    )
    db.add(event)
    return event


def verify_chain(db: Session) -> tuple[bool, int]:
    """Return (ok, jumlah event) dengan memeriksa seluruh rantai hash."""
    events = db.execute(select(AuditEvent).order_by(AuditEvent.id.asc())).scalars()
    prev = ""
    count = 0
    for event in events:
        body = canonical_json(event.payload)
        expected = hashlib.sha256((prev + body).encode("utf-8")).hexdigest()
        if expected != event.event_hash:
            return False, count
        if (event.prev_hash or "") != prev:
            return False, count
        prev = event.event_hash
        count += 1
    return True, count
