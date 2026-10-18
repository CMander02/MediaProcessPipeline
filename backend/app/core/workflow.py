"""Optional media stages. Transcription and saving are always required."""

from pydantic import BaseModel, ConfigDict, StrictBool


class WorkflowConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_analysis: StrictBool = True
    separate: StrictBool = True
    diarize: StrictBool = True
    speaker_review: StrictBool = True
    polish: StrictBool = True
    analyze: StrictBool = True
    summary: StrictBool = True
    mindmap: StrictBool = True
    detail: StrictBool = True


def snapshot_workflow(options: dict, settings) -> dict:
    """Merge task overrides once; unconfigured old clients retain subtitle-first behavior."""
    defaults = getattr(settings, "pipeline_workflow", None)
    override = options.get("workflow")
    if defaults is None and override is None:
        return dict(options)
    base = defaults.model_dump() if isinstance(defaults, WorkflowConfig) else dict(defaults or {})
    if override is not None:
        base.update(WorkflowConfig.model_validate(override).model_dump(exclude_unset=True))
    workflow = WorkflowConfig.model_validate(base)
    return {**options, "workflow": workflow.model_dump(), "force_asr": True}


def stage_enabled(task, stage: str) -> bool:
    return bool((task.options.get("workflow") or {}).get(stage, True))


def step_skipped(task, step: str) -> bool:
    if step == "analyze":
        return not any(stage_enabled(task, stage) for stage in ("analyze", "summary", "mindmap", "detail"))
    if step in {"separate", "speaker_review", "polish"}:
        return not stage_enabled(task, step)
    return False
