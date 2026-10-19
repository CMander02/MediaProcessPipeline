import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path, PureWindowsPath

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.workspace_lifecycle import run_in_thread, WorkspaceActivityMiddleware, drain_workspace_threads

from app.api.routes import (
    auth,
    filesystem,
    library,
    logs,
    pipeline,
    search,
    sync,
    system,
    tasks,
    voiceprints,
)
from app.api.routes import kb as kb_router
from app.api.routes import settings as settings_router
from app.core.config import get_settings
from app.core.paths import get_workspace_paths
from app.core.database import close_db, init_db
from app.core.logging_setup import setup_logging
from app.core.pipeline import process_task
from app.core.queue import get_task_queue
from app.core.security import request_is_authenticated
from app.core.settings import SETTINGS_FILE, get_runtime_settings
from app.version import __version__

# Force UTF-8 encoding for stdout/stderr on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_log_dir = get_workspace_paths().logs
_log_file = setup_logging(_log_dir)

logger = logging.getLogger(__name__)
if _log_file:
    logger.info(f"Logging to {_log_file}")

config = get_settings()

_NO_STORE_HEADERS = {"Cache-Control": "no-store"}


class NoStoreStaticFiles(StaticFiles):
    """Serve local frontend assets without browser caching stale Vite chunks."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers.update(_NO_STORE_HEADERS)
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    rt = get_runtime_settings()
    logger.info(f"Loaded runtime settings from {SETTINGS_FILE}")
    logger.info(f"  LLM Provider: {rt.llm_provider}")
    if rt.llm_provider == "custom":
        logger.info(f"  Custom Model: {rt.custom_model}")
        logger.info(f"  Custom API Base: {rt.custom_api_base}")

    # sherpa-onnx and onnxruntime-gpu ship separate ONNX Runtime DLL sets on
    # Windows. Load the selected sherpa CUDA runtime first so a later import of
    # onnxruntime (for VAD or other CPU sessions) cannot claim the provider DLL
    # names and make Qwen3-ASR fail with Windows error 1114.
    if (
        sys.platform == "win32"
        and rt.asr_provider == "sherpa_onnx"
        and rt.sherpa_device in {"auto", "cuda"}
    ):
        try:
            from app.services.recognition.sherpa_catalog import resolve_model
            from app.services.recognition.sherpa_runtime import (
                SherpaRuntimeOptions,
                get_sherpa_runtime,
            )

            spec = resolve_model(rt.sherpa_model_id, rt.sherpa_model_root)
            _, runtime_info = await run_in_thread(
                get_sherpa_runtime().get,
                spec,
                SherpaRuntimeOptions(
                    device=rt.sherpa_device,
                    num_threads=rt.sherpa_num_threads,
                    debug=rt.sherpa_debug,
                ),
            )
            logger.info(
                "Preloaded sherpa model %s with provider=%s before ONNX Runtime consumers",
                runtime_info.model_id,
                runtime_info.provider,
            )
        except Exception as exc:
            logger.warning("Sherpa runtime preload failed: %s", exc)

    # Initialize SQLite task store
    init_db()
    from app.core.archive_sync import recover_archive_imports
    await run_in_thread(recover_archive_imports, Path(rt.data_root))
    from app.core.archive_lifecycle import get_archive_lifecycle
    await run_in_thread(get_archive_lifecycle().recover)
    from app.core.library_batch import get_library_batch_service
    await get_library_batch_service().recover()

    # Startup maintenance can run after the health endpoint becomes available.
    from app.services.ingestion.ytdlp_version import auto_update_on_startup, warn_if_stale
    from app.core.archive_sync import get_archive_sync_service

    startup_maintenance = [
        asyncio.create_task(
            run_in_thread(
                auto_update_on_startup if rt.ytdlp_auto_update else warn_if_stale,
                *([True] if rt.ytdlp_auto_update else []),
            ),
            name="ytdlp-startup-maintenance",
        ),
        asyncio.create_task(
            run_in_thread(get_archive_sync_service().reconcile),
            name="archive-sync-reconcile",
        ),
    ]

    # Start task queue worker
    queue = get_task_queue()
    queue.set_pipeline(process_task)
    await queue.start()

    from app.services.remote_archive_upload import get_remote_archive_upload_service

    remote_archive_upload = get_remote_archive_upload_service()
    await remote_archive_upload.start()
    from app.services.remote_task_worker import get_remote_task_worker
    remote_task_worker = get_remote_task_worker()
    await remote_task_worker.start()

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
            await remote_task_worker.stop()
            await remote_archive_upload.stop()
            await queue.stop()
        finally:
            try:
                from app.services.analysis.local_llm_runtime import release_local_llm_runtime
                from app.services.recognition import release_asr_models

                await run_in_thread(release_asr_models)
                await run_in_thread(release_local_llm_runtime)
            except Exception as e:
                logger.warning("Runtime cleanup during shutdown failed: %s", e)
            # to_thread keeps running after its asyncio wrapper is cancelled.
            # Drain maintenance before closing the connection used by reconcile.
            await asyncio.gather(*startup_maintenance, return_exceptions=True)
            await drain_workspace_threads()
            close_db()

app = FastAPI(
    title=config.api_title,
    version=__version__,
    debug=config.debug,
    lifespan=lifespan,
)

app.add_middleware(WorkspaceActivityMiddleware)

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

_AUTH_EXEMPT_PATHS = {
    "/api/auth/status",
    "/api/auth/unlock",
    "/api/auth/logout",
    "/api/capabilities",
}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    # Only gate /api/* endpoints
    if path.startswith("/api"):
        # Bearer token auth (optional — only when api_token is configured)
        rt = get_runtime_settings()
        token = rt.api_token
        if token and request.method != "OPTIONS" and path not in _AUTH_EXEMPT_PATHS:
            if not request_is_authenticated(request, token):
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
app.include_router(library.router, prefix='/api')
app.include_router(pipeline.router, prefix="/api")
app.include_router(settings_router.router, prefix="/api")
app.include_router(filesystem.router, prefix="/api")
app.include_router(voiceprints.router, prefix="/api")
app.include_router(kb_router.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(sync.router, prefix="/api")
from app.api.routes import execution
app.include_router(execution.router, prefix="/api")
app.include_router(logs.router, prefix="/api")
app.include_router(system.router, prefix="/api")
app.include_router(search.router, prefix="/api")


@app.get("/health")
async def health_check():
    return {
        "status": "healthy", "service": config.api_title, "version": __version__,
        "app": "mpp", "pid": os.getpid(),
    }


# Serve frontend static files (built Vite output)
_web_dist = Path(__file__).resolve().parent.parent.parent / "web" / "dist"


def _static_file(path: str) -> Path | None:
    """Resolve a public asset without following paths outside the build directory."""
    # ASGI has already URL-decoded the path. Reject Windows syntax on all hosts
    # so a deployment change cannot turn a previously harmless URL into a path.
    if ("\\" in path or "\x00" in path or ":" in path
            or PureWindowsPath(path).drive or path.startswith("/")
            or ".." in path.split("/")):
        return None
    try:
        root = _web_dist.resolve(strict=True)
        candidate = (root / path).resolve()
        if not candidate.is_relative_to(root):
            return None
        return candidate
    except (OSError, ValueError, RuntimeError):
        return None


if _web_dist.is_dir():
    app.mount("/assets", NoStoreStaticFiles(directory=str(_web_dist / "assets")), name="static")

    # Serve static files in root (favicon, etc.)
    @app.get("/favicon.svg")
    async def favicon():
        file_path = _static_file("favicon.svg")
        if file_path is None or not file_path.is_file():
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        return FileResponse(file_path, headers=_NO_STORE_HEADERS)

    # SPA fallback: serve index.html for all non-API routes
    @app.get("/{path:path}")
    async def spa_fallback(path: str):
        if path == "api" or path.startswith("api/"):
            return JSONResponse(
                status_code=404,
                content={"detail": f"API route not found: /{path}"},
            )
        file_path = _static_file(path)
        if file_path is None:
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        if file_path.is_file():
            return FileResponse(file_path, headers=_NO_STORE_HEADERS)
        index = _static_file("index.html")
        if index is None or not index.is_file():
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        return FileResponse(index, headers=_NO_STORE_HEADERS)

