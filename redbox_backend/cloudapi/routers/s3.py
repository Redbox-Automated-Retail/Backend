"""S3 stand-in for presigned URLs (BITS downloads through the Update Client's 301 redirector, PUT uploads).

Objects live under DATA_DIR/s3/<key>. A presigned URL is
  {S3_BASE_URL}/s3/<key>?X-Amz-Expires=<unix>&X-Amz-Signature=<hmac-sha256(key|method|expires)>
signed with CERT_DIR/s3.key. HEAD/GET support Range (BITS); PUT stores the upload.
"""
import hashlib
import hmac
import time
import urllib.parse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

from ... import certs, config, db, filesets

router = APIRouter()


def _sign(key, method, expires):
    return hmac.new(certs.s3_key(), f"{key}|{method}|{expires}".encode(), hashlib.sha256).hexdigest()


def presign(key, method="GET", ttl=3600):
    key = key.lstrip("/")
    expires = int(time.time()) + ttl
    query = urllib.parse.urlencode({"X-Amz-Expires": expires, "X-Amz-Signature": _sign(key, method, expires)})
    return f"{config.S3_BASE_URL.rstrip('/')}/s3/{urllib.parse.quote(key)}?{query}"


def _check(request: Request, key, method):
    expires = request.query_params.get("X-Amz-Expires", "0")
    sig = request.query_params.get("X-Amz-Signature", "")
    if not expires.isdigit() or int(expires) < time.time():
        raise HTTPException(403, "expired")
    # A GET may use a URL signed for HEAD: the Update Client asks for IsHead=true even for GETs.
    if not any(hmac.compare_digest(sig, _sign(key, m, expires)) for m in ({method, "GET", "HEAD"})):
        raise HTTPException(403, "bad signature")


@router.api_route("/s3/{key:path}", methods=["GET", "HEAD"])
async def s3_get(key: str, request: Request):
    _check(request, key, request.method)
    root = filesets.s3_root().resolve()
    p = (root / key).resolve()
    if not str(p).startswith(str(root)) or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, media_type="application/octet-stream")


@router.put("/s3/{key:path}")
async def s3_put(key: str, request: Request):
    _check(request, key, "PUT")
    root = filesets.s3_root().resolve()
    p = (root / key).resolve()
    if not str(p).startswith(str(root)):
        raise HTTPException(400)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = await request.body()
    p.write_bytes(data)
    await db.record_event(None, "s3", "upload", {"key": key, "size": len(data)})
    return Response(status_code=200, headers={"ETag": '"' + hashlib.md5(data).hexdigest() + '"'})
