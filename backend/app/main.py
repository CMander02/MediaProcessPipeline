import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import tasks, pipeline
from app.api.routes import settings as settings_router
from app.api.routes.settings import get_runtime_settings, SETTINGS_FILE
from app.core.config import get_settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

config = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: load runtime settings from file
    rt = get_runtime_settings()
    logger.info(f"Loaded runtime settings from {SETTINGS_FILE}")
    logger.info(f"  LLM Provider: {rt.llm_provider}")
    if rt.llm_provider == "custom":
        logger.info(f"  Custom Model: {rt.custom_model}")
        logger.info(f"  Custom API Base: {rt.custom_api_base}")
    yield
    # Shutdown: nothing to clean up

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


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": config.api_title, "version": config.api_version}
