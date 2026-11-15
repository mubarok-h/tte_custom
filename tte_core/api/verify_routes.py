from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..config import get_settings
from ..db import get_db
from ..verification import validate_pdf_bytes
from ..verify_service import VerificationError, verify_document
from .deps import require_auth
from .schemas import VerifyOut

router = APIRouter(prefix="/api/v1", tags=["verification"])


@router.get("/verify/{short_id}", response_model=VerifyOut)
def verify_by_short_id(
    short_id: str,
    t: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
) -> VerifyOut:
    try:
        result = verify_document(db, get_settings(), short_id, t)
        db.commit()
        return VerifyOut(**result)
    except VerificationError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/verify", response_model=VerifyOut)
async def verify_upload(
    file: UploadFile = File(...),
    _: None = Depends(require_auth),
) -> VerifyOut:
    settings = get_settings()
    data = await file.read()
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="Ukuran file melebihi batas.")
    result = await run_in_threadpool(validate_pdf_bytes, data, settings)
    return VerifyOut(**result)
