import asyncio
import hashlib
import hmac
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from app.api.routes import filesystem, pipeline, sync, system, tasks, voiceprints
from app.api.routes import kb as kb_router
from app.api.routes import settings as settings_router
from app.core.config import get_settings
from app.core.database import close_db, init_db
from app.core.logging_setup import setup_logging
from app.core.paths import resolve_runtime_paths, validate_web_dist
from app.core.pipeline import process_task
from app.core.queue import get_task_queue
from app.core.settings import SETTINGS_FILE, get_runtime_settings
from app.version import APP_VERSION

# Force UTF-8 encoding for stdout/stderr on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_runtime_paths = resolve_runtime_paths()
_log_dir = _runtime_paths.log_dir
_log_file = setup_logging(_log_dir)

logger = logging.getLogger(__name__)
if _log_file:
    logger.info(f"Logging to {_log_file}")

config = get_settings()

_NO_STORE_HEADERS = {"Cache-Control": "no-store"}
DESKTOP_HEALTH_PRODUCT = "com.mpp.backend"
DESKTOP_HEALTH_PROTOCOL = 1
DESKTOP_HEALTH_SERVICE = "Media Process Pipeline"
_DESKTOP_SESSION_ENV = "MPP_DESKTOP_SESSION_TOKEN"
_DESKTOP_NONCE_HEADER = "x-mpp-desktop-nonce"
_DESKTOP_SESSION_HEADER = "x-mpp-desktop-session"
_DESKTOP_SHUTDOWN_PATH = "/api/desktop/shutdown"
_DESKTOP_SESSION_SECRET = os.environ.pop(_DESKTOP_SESSION_ENV, "")


def _desktop_session_secret() -> str:
    secret = _DESKTOP_SESSION_SECRET
    if secret and (
        len(secret) != 64
        or any(character not in "0123456789abcdef" for character in secret)
    ):
        raise RuntimeError(
            f"{_DESKTOP_SESSION_ENV} must contain 32 random bytes as lowercase hex"
        )
    return secret


def _desktop_health_message(nonce: str) -> bytes:
    return "\0".join(
        (
            nonce,
            DESKTOP_HEALTH_PRODUCT,
            str(DESKTOP_HEALTH_PROTOCOL),
            DESKTOP_HEALTH_SERVICE,
            APP_VERSION,
        )
    ).encode("utf-8")


def _desktop_health_proof(secret: str, nonce: str) -> str:
    return hmac.new(
        secret.encode("ascii"),
        _desktop_health_message(nonce),
        hashlib.sha256,
    ).hexdigest()


class NoStoreStaticFiles(StaticFiles):
    """Serve local frontend assets without browser caching stale Vite chunks."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers.update(_NO_STORE_HEADERS)
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    _desktop_session_secret()
    rt = get_runtime_settings()
    if _api_token_is_required() and not rt.api_token:
        raise RuntimeError(
            "MPP_REQUIRE_API_TOKEN is enabled, but api_token is empty or unavailable"
        )
    logger.info(f"Loaded runtime settings from {SETTINGS_FILE}")
    logger.info(f"  LLM Provider: {rt.llm_provider}")
    if rt.llm_provider == "custom":
        logger.info(f"  Custom Model: {rt.custom_model}")
        logger.info(f"  Custom API Base: {rt.custom_api_base}")

    from app.services.ingestion.ytdlp_version import auto_update_on_startup, warn_if_stale
    if rt.ytdlp_auto_update:
        await asyncio.to_thread(auto_update_on_startup, True)
    else:
        asyncio.create_task(asyncio.to_thread(warn_if_stale))

    # Initialize SQLite task store
    init_db()

    try:
        from app.core.archive_sync import sweep_stale_sync_storage

        sync_cleanup = sweep_stale_sync_storage(Path(rt.data_root))
        if sync_cleanup["removed"] or sync_cleanup["restored"]:
            logger.info(
                "Sync storage recovery: removed=%s restored=%s",
                sync_cleanup["removed"],
                sync_cleanup["restored"],
            )
    except Exception as e:
        logger.warning("Sync storage recovery failed: %s", e)

    # Start task queue worker
    queue = get_task_queue()
    queue.set_pipeline(process_task)
    await queue.start()

    from app.services.remote_sync import get_remote_sync_service

    remote_sync = get_remote_sync_service()
    await remote_sync.start()

    # Sweep stale upload staging dirs (>24h old, never confirmed by user)
    try:
        from app.api.routes.pipeline import sweep_stale_staging
        removed = sweep_stale_staging()
        if removed:
            logger.info(f"Swept {removed} stale staging dir(s)")
    except Exception as e:
        logger.warning(f"Staging sweep failed: {e}")

    try:
        yield
    finally:
        # Shutdown persistent workers and model-server child processes before
        # closing the database. The desktop process job remains the hard-stop
        # fallback for interrupted Windows exits.
        try:
            await remote_sync.stop()
        finally:
            try:
                await queue.stop()
            finally:
                try:
                    from app.services.analysis.local_llm_runtime import release_local_llm_runtime
                    from app.services.recognition import release_asr_models

                    await asyncio.to_thread(release_asr_models)
                    await asyncio.to_thread(release_local_llm_runtime)
                except Exception as e:
                    logger.warning("Runtime cleanup during shutdown failed: %s", e)
                close_db()

app = FastAPI(
    title=config.api_title,
    version=APP_VERSION,
    debug=config.debug,
    lifespan=lifespan,
)

_cors_origins = list(config.cors_origins)
if _DESKTOP_SESSION_SECRET:
    _cors_origins.extend(
        [
            "tauri://localhost",
            "http://tauri.localhost",
        ]
    )

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# API token authentication middleware
# ---------------------------------------------------------------------------
# When api_token is set in runtime settings, all /api/* requests must carry
# a matching Bearer token. Static assets, /health, and frontend routes are
# exempt so the SPA still loads.

_AUTH_EXEMPT_PREFIXES = ("/health", "/assets", "/favicon")


def _api_token_is_required() -> bool:
    return os.environ.get("MPP_REQUIRE_API_TOKEN", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path == "/health":
        try:
            expected_session = _desktop_session_secret()
        except RuntimeError:
            return JSONResponse(
                status_code=503,
                content={"detail": "Desktop health proof is unavailable"},
                headers=_NO_STORE_HEADERS,
            )
        if expected_session and _DESKTOP_NONCE_HEADER in request.headers:
            nonce = request.headers[_DESKTOP_NONCE_HEADER]
            if len(nonce) != 64 or any(
                character not in "0123456789abcdef" for character in nonce
            ):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid desktop health challenge"},
                    headers=_NO_STORE_HEADERS,
                )
            request.state.desktop_health_nonce = nonce

    # Only gate /api/* endpoints
    if path.startswith("/api"):
        # Bearer token auth. Production coordinators can force fail-closed
        # behavior independently of the runtime settings file.
        rt = get_runtime_settings()
        token = rt.api_token
        try:
            desktop_secret = _desktop_session_secret()
        except RuntimeError:
            return JSONResponse(
                status_code=503,
                content={"detail": "Desktop session authentication is unavailable"},
            )
        supplied_desktop_session = request.headers.get(_DESKTOP_SESSION_HEADER, "")
        desktop_session_matches = bool(desktop_secret) and hmac.compare_digest(
            supplied_desktop_session,
            desktop_secret,
        )
        if path == _DESKTOP_SHUTDOWN_PATH and not desktop_session_matches:
            return JSONResponse(
                status_code=401,
                content={"detail": "Unauthorized desktop shutdown request"},
            )
        if _api_token_is_required() and not token:
            return JSONResponse(
                status_code=503,
                content={"detail": "API authentication is required but unavailable"},
            )
        if token and not desktop_session_matches:
            auth_header = request.headers.get("authorization", "")
            cookie_token = request.cookies.get("mpp_api_token", "")
            bearer_matches = hmac.compare_digest(auth_header, f"Bearer {token}")
            cookie_matches = hmac.compare_digest(cookie_token, token)
            if not bearer_matches and not cookie_matches:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Unauthorized — invalid or missing Bearer token"},
                )

        # CSRF protection: non-GET requests must carry X-Requested-With header.
        # Browsers block custom headers on cross-origin "simple" requests,
        # so a malicious page cannot forge POST/PUT/DELETE/PATCH to our API.
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if not request.headers.get("x-requested-with"):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Missing X-Requested-With header"},
                )

    response = await call_next(request)
    if path == "/health":
        response.headers.update(_NO_STORE_HEADERS)
    return response


# Include routers
app.include_router(tasks.router, prefix="/api")
app.include_router(pipeline.router, prefix="/api")
app.include_router(settings_router.router, prefix="/api")
app.include_router(filesystem.router, prefix="/api")
app.include_router(voiceprints.router, prefix="/api")
app.include_router(kb_router.router, prefix="/api")
app.include_router(sync.router, prefix="/api")
app.include_router(system.router, prefix="/api")


@app.post(_DESKTOP_SHUTDOWN_PATH)
async def desktop_shutdown(request: Request):
    callback = getattr(request.app.state, "request_desktop_shutdown", None)
    if not callable(callback):
        return JSONResponse(
            status_code=404,
            content={"detail": "Desktop shutdown controller is unavailable"},
        )
    return JSONResponse(
        content={"status": "shutting_down"},
        headers=_NO_STORE_HEADERS,
        background=BackgroundTask(callback),
    )


@app.get("/health")
async def health_check(request: Request):
    payload = {
        "status": "healthy",
        "service": DESKTOP_HEALTH_SERVICE,
        "version": APP_VERSION,
        "product": DESKTOP_HEALTH_PRODUCT,
        "protocol": DESKTOP_HEALTH_PROTOCOL,
    }
    secret = _desktop_session_secret()
    nonce = getattr(request.state, "desktop_health_nonce", "")
    if secret and nonce:
        payload["desktopProof"] = _desktop_health_proof(secret, nonce)
    return payload


# Serve frontend static files (built Vite output)
_web_dist = validate_web_dist(
    _runtime_paths.web_dist_dir,
    required=_runtime_paths.installed_mode
    or bool(os.environ.get("MPP_WEB_DIST_DIR", "").strip()),
)
if _web_dist is not None:
    app.mount("/assets", NoStoreStaticFiles(directory=str(_web_dist / "assets")), name="static")

    # Serve static files in root (favicon, etc.)
    @app.get("/favicon.svg")
    async def favicon():
        return FileResponse(_web_dist / "favicon.svg", headers=_NO_STORE_HEADERS)

    # SPA fallback: serve index.html for all non-API routes
    @app.get("/{path:path}")
    async def spa_fallback(path: str):
        if path == "api" or path.startswith("api/"):
            return JSONResponse(
                status_code=404,
                content={"detail": f"API route not found: /{path}"},
            )
        file_path = _web_dist / path
        if file_path.is_file():
            return FileResponse(file_path, headers=_NO_STORE_HEADERS)
        return FileResponse(_web_dist / "index.html", headers=_NO_STORE_HEADERS)

