import logging
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from app.api.routes import tasks, pipeline, filesystem
from app.api.routes import settings as settings_router
from app.core.settings import get_runtime_settings, SETTINGS_FILE
from app.core.config import get_settings
from app.core.database import init_db, close_db
from app.core.events import get_event_bus
from app.core.pipeline import process_task
from app.core.queue import get_task_queue

# Force UTF-8 encoding for stdout/stderr on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Unified log format with UTC timestamps
_log_fmt = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"
_log_datefmt = "%Y-%m-%dT%H:%M:%SZ"
logging.Formatter.converter = time.gmtime

_handler = logging.StreamHandler(sys.stderr)
_handler.setFormatter(logging.Formatter(_log_fmt, datefmt=_log_datefmt))
logging.root.setLevel(logging.INFO)
logging.root.handlers.clear()
logging.root.addHandler(_handler)

logger = logging.getLogger(__name__)

# Suppress noisy third-party loggers
for _noisy in ("httpx", "httpcore", "openai", "uvicorn.access"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

config = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    rt = get_runtime_settings()
    logger.info(f"Loaded runtime settings from {SETTINGS_FILE}")
    logger.info(f"  LLM Provider: {rt.llm_provider}")
    if rt.llm_provider == "custom":
        logger.info(f"  Custom Model: {rt.custom_model}")
        logger.info(f"  Custom API Base: {rt.custom_api_base}")

    # Initialize SQLite task store
    init_db()

    # Start task queue worker
    queue = get_task_queue()
    queue.set_pipeline(process_task)
    await queue.start()

    yield

    # Shutdown
    await queue.stop()
    close_db()

app = FastAPI(
    title=config.api_title,
    version=config.api_version,
    debug=config.debug,
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_origins,
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


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    # Only gate /api/* endpoints
    if path.startswith("/api"):
        # Bearer token auth (optional — only when api_token is configured)
        rt = get_runtime_settings()
        token = rt.api_token
        if token:
            auth_header = request.headers.get("authorization", "")
            if auth_header != f"Bearer {token}":
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

    return await call_next(request)


# Include routers
app.include_router(tasks.router, prefix="/api")
app.include_router(pipeline.router, prefix="/api")
app.include_router(settings_router.router, prefix="/api")
app.include_router(filesystem.router, prefix="/api")


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": config.api_title, "version": config.api_version}


# Serve frontend static files (built Vite output)
_web_dist = Path(__file__).resolve().parent.parent.parent / "web" / "dist"
if _web_dist.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_web_dist / "assets")), name="static")

    # Serve static files in root (favicon, etc.)
    @app.get("/favicon.svg")
    async def favicon():
        return FileResponse(_web_dist / "favicon.svg")

    # SPA fallback: serve index.html for all non-API routes
    @app.get("/{path:path}")
    async def spa_fallback(path: str):
        file_path = _web_dist / path
        if file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(_web_dist / "index.html")

