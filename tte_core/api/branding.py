from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..branding import IMAGE_EXTENSIONS, branding_dir

router = APIRouter(tags=["branding"])


@router.get("/branding/{filename}")
def branding_file(filename: str):
    name = Path(filename).name
    if name != filename or Path(name).suffix.lower() not in IMAGE_EXTENSIONS:
        raise HTTPException(status_code=404, detail="Berkas tidak ditemukan.")
    path = branding_dir() / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Berkas tidak ditemukan.")
    return FileResponse(
        path,
        headers={"Cache-Control": "public, max-age=86400"},
    )
