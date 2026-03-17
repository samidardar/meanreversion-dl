"""
Security layer for the AI Call Center API.

Features:
- API key authentication (Bearer token or X-API-Key header)
- In-memory rate limiting per IP
- Input sanitization helpers
- File upload validation
"""
import time
import secrets
import logging
from collections import defaultdict
from typing import Optional
from fastapi import Request, HTTPException, Security, UploadFile
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from config.settings import settings

logger = logging.getLogger(__name__)

# ─── API Key Auth ─────────────────────────────────────────────────────────────

_security = HTTPBearer(auto_error=False)

# Multiple API keys supported (comma-separated in env)
def _load_api_keys() -> set[str]:
    raw = getattr(settings, "api_keys", "") or ""
    keys = {k.strip() for k in raw.split(",") if k.strip()}
    # Also support single API_KEY
    single = getattr(settings, "api_key", "") or ""
    if single:
        keys.add(single.strip())
    return keys


def verify_api_key(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Security(_security),
) -> str:
    """
    FastAPI dependency. Accepts API key via:
    - Authorization: Bearer <key>
    - X-API-Key: <key>
    - ?api_key=<key>  (query param, for WebSocket auth)

    If no API keys are configured, auth is disabled (dev mode).
    """
    configured_keys = _load_api_keys()

    # Dev mode: no keys configured → allow all
    if not configured_keys:
        return "dev"

    # Check Authorization header
    if credentials and credentials.credentials in configured_keys:
        return credentials.credentials

    # Check X-API-Key header
    header_key = request.headers.get("X-API-Key", "")
    if header_key in configured_keys:
        return header_key

    # Check query param (for WebSocket connections)
    query_key = request.query_params.get("api_key", "")
    if query_key in configured_keys:
        return query_key

    logger.warning("Unauthorized API access attempt from %s", request.client.host if request.client else "unknown")
    raise HTTPException(
        status_code=401,
        detail="Invalid or missing API key",
        headers={"WWW-Authenticate": "Bearer"},
    )


# ─── Rate Limiting ────────────────────────────────────────────────────────────

class RateLimiter:
    """Simple sliding-window rate limiter stored in memory."""

    def __init__(self, max_requests: int = 60, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window = window_seconds
        self._store: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, key: str) -> bool:
        now = time.time()
        window_start = now - self.window
        # Keep only requests within window
        self._store[key] = [t for t in self._store[key] if t > window_start]
        if len(self._store[key]) >= self.max_requests:
            return False
        self._store[key].append(now)
        return True

    def remaining(self, key: str) -> int:
        now = time.time()
        window_start = now - self.window
        recent = [t for t in self._store[key] if t > window_start]
        return max(0, self.max_requests - len(recent))


# Rate limiters for different endpoint groups
_api_limiter = RateLimiter(max_requests=120, window_seconds=60)   # general API
_upload_limiter = RateLimiter(max_requests=10, window_seconds=60)  # file uploads
_call_limiter = RateLimiter(max_requests=30, window_seconds=60)    # call initiation


def rate_limit(limiter: RateLimiter = _api_limiter):
    """FastAPI dependency factory for rate limiting."""
    def _check(request: Request):
        ip = request.client.host if request.client else "unknown"
        if not limiter.is_allowed(ip):
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please slow down.",
                headers={"Retry-After": "60"},
            )
    return _check


api_rate_limit = rate_limit(_api_limiter)
upload_rate_limit = rate_limit(_upload_limiter)
call_rate_limit = rate_limit(_call_limiter)


# ─── File Upload Validation ────────────────────────────────────────────────────

MAX_UPLOAD_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls"}


async def validate_upload(file: UploadFile) -> bytes:
    """
    Validates and reads an uploaded file.
    Checks: file extension, content type, size limit.
    Returns file bytes if valid.
    """
    filename = file.filename or ""
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type '{ext}'. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    allowed_content_types = {
        "text/csv",
        "application/csv",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/octet-stream",  # some clients send this for CSV
    }
    if file.content_type and file.content_type not in allowed_content_types:
        logger.warning("Suspicious content-type for upload: %s", file.content_type)
        # Don't hard block — content-type is client-supplied and unreliable

    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size is {MAX_UPLOAD_SIZE_BYTES // (1024*1024)} MB.",
        )
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    return content


# ─── Input Sanitization ───────────────────────────────────────────────────────

def sanitize_string(value: str, max_length: int = 500) -> str:
    """Strip and truncate string input."""
    if not isinstance(value, str):
        return ""
    return value.strip()[:max_length]


def sanitize_phone(phone: str) -> str:
    """Validate E.164 phone format."""
    import re
    cleaned = re.sub(r"\D", "", phone)
    if len(cleaned) < 7 or len(cleaned) > 15:
        raise HTTPException(status_code=422, detail=f"Invalid phone number: {phone}")
    if not cleaned.startswith("+"):
        # Best-effort: assume North American if 10 digits
        if len(cleaned) == 10:
            return f"+1{cleaned}"
        return f"+{cleaned}"
    return phone
