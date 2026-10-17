"""Task and media processing models."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class TaskStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    PROCESSING = "processing"
    PAUSED = "paused"
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


class ChapterInfo(BaseModel):
    """Video chapter/timestamp marker."""
    title: str
    start_time: float  # seconds


class MediaMetadata(BaseModel):
    title: str
    source_url: str | None = None
    uploader: str | None = None
    uploader_id: str | None = None  # platform UID (bili mid, xhs userId, yt channel_id, …)
    platform: str | None = None     # bilibili / youtube / xiaohongshu / xiaoyuzhou / apple_podcast / local / generic
    upload_date: datetime | None = None
    duration_seconds: float | None = None
    media_type: MediaType = MediaType.OTHER
    content_subtype: str | None = None  # video / audio / image_note / podcast_episode / local_file …
    file_path: str | None = None
    file_hash: str | None = None
    # Extended metadata from yt-dlp
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    chapters: list[ChapterInfo] = Field(default_factory=list)
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


class CollectionFolderImport(BaseModel):
    source: str
    create_folder: bool = False


class TaskBatchCreate(BaseModel):
    task_type: TaskType = TaskType.PIPELINE
    sources: list[str] = Field(min_length=1, max_length=100)
    options: dict[str, Any] = Field(default_factory=dict)
    webhook_url: str | None = None
    folder_id: str | None = None
    move_existing: bool = False
    collection_folders: list[CollectionFolderImport] = Field(default_factory=list, max_length=100)


class Task(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    task_type: TaskType
    status: TaskStatus = TaskStatus.PENDING
    source: str
    external_source: bool = False
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
    flow: dict[str, Any] | None = None
    # Denormalized media metadata columns (mirrors tasks DB columns)
    platform: str | None = None
    uploader_id: str | None = None
    content_subtype: str | None = None


class TaskImportItem(BaseModel):
    source: str
    content_key: str | None = None
    outcome: Literal["created", "reused", "skipped", "failed"]
    task: Task | None = None
    match: dict[str, Any] | None = None
    reason: str | None = None
    error: str | None = None
    folder_id: str | None = None
    folder_outcome: str | None = None
    folder_error: str | None = None


class TaskImportResponse(BaseModel):
    items: list[TaskImportItem]
