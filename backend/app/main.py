from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .api import auth_router, billing_router, router
from .body_limit import build_body_size_limit_middleware
from .config import settings
from .database import SessionLocal, engine, initialize_database
from .observability import RequestIDMiddleware, request_id_var, setup_logging
from .rate_limit import RateLimitExceeded

initialize_database()
setup_logging(settings.log_level)

app = FastAPI(title=settings.app_name, version="0.1.0")


@app.exception_handler(RateLimitExceeded)
async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Return HTTP 429 with Retry-After and the active X-Request-ID."""
    request_id = request_id_var.get() or request.headers.get("X-Request-ID") or "-"
    return JSONResponse(
        status_code=429,
        content={"detail": str(exc)},
        headers={
            "Retry-After": f"{max(int(exc.retry_after), 1)}",
            "X-Request-ID": request_id,
        },
    )


@app.middleware("http")
async def production_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-XSS-Protection", "0")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'",
    )
    return response


# Placed inside RequestIDMiddleware so oversized requests still receive a
# generated X-Request-ID and are logged like any other request.
app.add_middleware(
    build_body_size_limit_middleware(settings.max_upload_size_mb * 1024 * 1024)
)
app.add_middleware(RequestIDMiddleware)

origins = [origin.strip() for origin in settings.allowed_origins.split(",") if origin.strip()]
if origins:
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
hosts = [host.strip() for host in settings.allowed_hosts.split(",") if host.strip()]
if settings.app_environment.lower() == "production" and hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
app.include_router(router)
app.include_router(auth_router)
app.include_router(billing_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}


@app.get("/ready")
def readiness() -> JSONResponse:
    """Lightweight production readiness probe.

    Verifies that the database connection is alive without exposing
    any sensitive information.  A non-200 response signals the
    orchestrator that the container is not yet ready to receive traffic.
    """
    try:
        with SessionLocal() as db:
            from sqlalchemy import text
            db.execute(text("SELECT 1"))
        return JSONResponse(status_code=200, content={"status": "ready"})
    except Exception:
        return JSONResponse(status_code=503, content={"status": "unavailable"})


frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
