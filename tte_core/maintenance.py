from __future__ import annotations

import argparse
import shutil
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from .audit import append_event, verify_chain
from .config import get_settings
from .db import get_session_factory, init_db
from .models import Document


def audit_check() -> int:
    init_db()
    session = get_session_factory()()
    try:
        ok, count = verify_chain(session)
        print(f"audit_chain_ok={ok} events={count}")
        return 0 if ok else 1
    finally:
        session.close()


def archive_expired() -> int:
    init_db()
    settings = get_settings()
    session = get_session_factory()()
    now = datetime.now(timezone.utc)
    try:
        docs = session.execute(
            select(Document).where(
                Document.archived_at.is_(None),
                Document.retention_until < now,
            )
        ).scalars()
        archived = 0
        for doc in docs:
            target = settings.data_dir / "archive" / doc.id
            target.mkdir(parents=True, exist_ok=True)
            for attr in ("original_path", "signed_path"):
                current = getattr(doc, attr)
                if not current:
                    continue
                source = Path(current)
                if source.exists():
                    destination = target / source.name
                    shutil.move(str(source), str(destination))
                    setattr(doc, attr, str(destination))
            doc.archived_at = now
            doc.status = "archived"
            append_event(
                session,
                "document.archived",
                {"document_id": doc.id, "short_id": doc.short_id},
            )
            archived += 1
        session.commit()
        print(f"archived={archived}")
        return 0
    finally:
        session.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Pemeliharaan TTE")
    parser.add_argument(
        "command", choices=["audit", "archive"], help="audit | archive"
    )
    args = parser.parse_args()
    if args.command == "audit":
        return audit_check()
    return archive_expired()


if __name__ == "__main__":
    raise SystemExit(main())
