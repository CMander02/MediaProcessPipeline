from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import tasks, pipeline
from app.api.routes import settings as settings_router
from app.core.config import get_settings

config = get_settings()

app = FastAPI(
    title=config.api_title,
    version=config.api_version,
    debug=config.debug,
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
