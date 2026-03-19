import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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

logging.basicConfig(level=logging.INFO, encoding="utf-8")
logger = logging.getLogger(__name__)

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

# Include routers
app.include_router(tasks.router, prefix="/api")
app.include_router(pipeline.router, prefix="/api")
app.include_router(settings_router.router, prefix="/api")
app.include_router(filesystem.router, prefix="/api")


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": config.api_title, "version": config.api_version}


def mount_gradio_ui(fastapi_app):
    """Mount Gradio UI at /ui. Called from serve.py before uvicorn.run()."""
    try:
        import gradio as gr
        from app.ui.app import create_ui

        blocks = create_ui()
        gr.mount_gradio_app(fastapi_app, blocks, path="/ui", ssr_mode=False)
        logger.info("Gradio UI mounted at /ui")
    except ImportError:
        logger.info("Gradio not installed, UI disabled")
    except Exception as e:
        logger.warning(f"Failed to mount Gradio UI: {e}", exc_info=True)
