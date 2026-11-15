from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from tte_core.audit import append_event, verify_chain
from tte_core.db import Base


def test_audit_chain_detects_tampering(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'audit.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()

    append_event(db, "document.uploaded", {"id": "1"})
    append_event(db, "sign_request.created", {"id": "2"})
    append_event(db, "document.signed", {"id": "3"})
    db.commit()

    ok, count = verify_chain(db)
    assert ok is True
    assert count == 3

    from tte_core.models import AuditEvent

    event = db.execute(
        select(AuditEvent).where(AuditEvent.event_type == "document.signed")
    ).scalar_one()
    event.payload = {"id": "3", "diubah": True}
    db.commit()

    ok, _ = verify_chain(db)
    assert ok is False
