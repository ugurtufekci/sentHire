"""Upload endpoint for the local storage backend (development and demos only).

In production the browser PUTs straight to S3 with a presigned URL and the API
never sees file bytes. With `SENTHIRE_STORAGE_BACKEND=local` there is no S3, so
this router stands in for it: same contract from the client's point of view,
bytes landing on disk instead.

The trade-off is explicit: a presigned S3 URL carries a signature, and this
does not. What guards it instead is the key shape — only keys this application
minted (`org/<uuid>/jobs/<uuid>/uploads/<uuid>/<file>`) are accepted, so the
endpoint cannot be used to write anywhere else — plus the same size cap uploads
have everywhere. That is adequate for a laptop and a demo box. It is not
adequate for the public internet, and this router is only mounted when the
local backend is deliberately enabled.
"""

from fastapi import APIRouter, HTTPException, Request, Response

from senthire.config import get_settings
from senthire.services import storage

router = APIRouter(tags=["storage"])


@router.put("/storage/{key:path}", status_code=204)
async def put_object(key: str, request: Request) -> Response:
    settings = get_settings()
    body = await request.body()
    if len(body) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="file too large")
    try:
        storage.put_object_bytes(key, body)
    except ValueError as exc:  # not a key this application minted
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(status_code=204)


@router.get("/storage/{key:path}")
def get_object(key: str) -> Response:
    try:
        data = storage.get_object_bytes(key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="object not found") from exc
    return Response(content=data, media_type="application/pdf")
