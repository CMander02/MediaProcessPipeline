"""Task and media processing models."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class TaskStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskType(StrEnum):
    INGESTION = "ingestion"
    PREPROCESSING = "preprocessing"
    RECOGNITION = "recognition"
    ANALYSIS = "analysis"
    ARCHIVING = "archiving"
    PIPELINE = "pipeline"


class MediaType(StrEnum):
    VIDEO = "video"
    AUDIO = "audio"
    PODCAST = "podcast"
    MEETING = "meeting"
    OTHER = "other"


class MediaMetadata(BaseModel):
    title: str
    source_url: str | None = None
    uploader: str | None = None
    upload_date: datetime | None = None
    duration_seconds: float | None = None
    media_type: MediaType = MediaType.OTHER
    file_path: str | None = None
    file_hash: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str
    speaker: str | None = None
    confidence: float | None = None


class TaskCreate(BaseModel):
    task_type: TaskType
    source: str
    options: dict[str, Any] = Field(default_factory=dict)
    webhook_url: str | None = None


class Task(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    task_type: TaskType
    status: TaskStatus = TaskStatus.PENDING
    source: str
    options: dict[str, Any] = Field(default_factory=dict)
    progress: float = 0.0
    message: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    webhook_url: str | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    completed_at: datetime | None = None
    # Step-based progress tracking
    current_step: str | None = None
    steps: list[str] = Field(default_factory=list)
    completed_steps: list[str] = Field(default_factory=list)
