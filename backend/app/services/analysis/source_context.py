"""Build and validate a source-grounded context shared by all text stages."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

from app.core.logging_setup import log_event
from app.models import MediaMetadata
from app.services.analysis.prompts.source_context import (
    SOURCE_CONTEXT_SYSTEM_PROMPT,
    get_source_context_prompt,
    get_source_context_repair_prompt,
)

logger = logging.getLogger(__name__)

_CONTEXT_VERSION = 1
_LATIN_ENTITY_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Z][A-Za-z0-9]*(?:[.-][A-Za-z0-9]+)*"
    r"(?:\s+[A-Z][A-Za-z0-9]*(?:[.-][A-Za-z0-9]+)*)*|"
    r"[A-Z]{2,}(?:[A-Za-z0-9.-]*))(?![A-Za-z0-9])"
)
_ROLE_PATTERNS = (
    ("host", re.compile(r"(?:主持人|主播|Host|导游)\s*[:：]\s*([\u4e00-\u9fff]{2,4})", re.I)),
    (
        "guest",
        re.compile(
            r"(?:嘉宾|对谈嘉宾|Guest|(?:\d+\s*号)?珍藏)\s*[:：]\s*([\u4e00-\u9fff]{2,4})",
            re.I,
        ),
    ),
)
_TITLE_GUEST_RE = re.compile(
    r"(?:对谈|访谈)\s*(?:[A-Za-z][A-Za-z0-9 .&+-]{1,60}\s*)?"
    r"(?:联创|联合创始人|创始人|CEO|CTO|研究员|教授)?\s*"
    r"([\u4e00-\u9fff]{2,4})(?:\s|$|[｜|：:，,])"
)


class TimelineItem(BaseModel):
    start: float = Field(ge=0)
    title: str = Field(min_length=1)
    source: str = Field(min_length=1)
    evidence: str = ""


class SourceEntity(BaseModel):
    canonical: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    type: Literal["person", "organization", "product", "model", "paper", "term", "other"] = "other"
    evidence: str = Field(min_length=1)
    confidence: float = Field(default=0.8, ge=0, le=1)


class SpeakerCandidate(BaseModel):
    name: str = Field(min_length=1)
    role: Literal["host", "guest", "speaker", "unknown"] = "unknown"
    evidence: str = Field(min_length=1)
    confidence: float = Field(default=0.8, ge=0, le=1)


class SpeakerCountHint(BaseModel):
    exact: int | None = Field(default=None, ge=1, le=32)
    min: int | None = Field(default=None, ge=1, le=32)
    max: int | None = Field(default=None, ge=1, le=32)
    confidence: float = Field(default=0, ge=0, le=1)
    evidence: str = ""

    @model_validator(mode="after")
    def validate_bounds(self) -> "SpeakerCountHint":
        if self.exact is not None:
            self.min = self.exact
            self.max = self.exact
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("speaker_count_hint.min must be <= max")
        return self


class SourceContextPatch(BaseModel):
    language_hint: str = "unknown"
    content_type: str = "unknown"
    entities: list[SourceEntity] = Field(default_factory=list)
    speaker_candidates: list[SpeakerCandidate] = Field(default_factory=list)
    speaker_count_hint: SpeakerCountHint = Field(default_factory=SpeakerCountHint)


class SourceContext(SourceContextPatch):
    version: int = _CONTEXT_VERSION
    title: str
    uploader: str = ""
    timeline: list[TimelineItem] = Field(default_factory=list)
    asr_hotwords: list[str] = Field(default_factory=list)
    source_signature: str = ""


def _metadata_dict(metadata: MediaMetadata | dict[str, Any]) -> dict[str, Any]:
    if isinstance(metadata, MediaMetadata):
        return metadata.model_dump(mode="json")
    return dict(metadata)


def _source_evidence(metadata: dict[str, Any]) -> str:
    chapters = metadata.get("chapters") or []
    chapter_titles = [str(item.get("title") or "") for item in chapters if isinstance(item, dict)]
    return "\n".join(
        [
            str(metadata.get("title") or ""),
            str(metadata.get("uploader") or ""),
            str(metadata.get("description") or ""),
            "\n".join(str(item) for item in metadata.get("tags") or []),
            "\n".join(chapter_titles),
        ]
    )


def _entity_evidence(metadata: dict[str, Any]) -> str:
    """Keep editorial credits and platform tags out of the ASR vocabulary."""
    description = str(metadata.get("description") or "")
    description = re.split(
        r"【(?:The gang that made this happen|制作团队|幕后团队)】",
        description,
        maxsplit=1,
        flags=re.I,
    )[0]
    chapters = metadata.get("chapters") or []
    chapter_titles = [
        str(item.get("title") or "") for item in chapters if isinstance(item, dict)
    ]
    return "\n".join(
        [
            str(metadata.get("title") or ""),
            str(metadata.get("uploader") or ""),
            description,
            "\n".join(chapter_titles),
        ]
    )


def _signature(metadata: dict[str, Any], task_options: dict[str, Any]) -> str:
    payload = {
        "version": _CONTEXT_VERSION,
        "title": metadata.get("title"),
        "uploader": metadata.get("uploader"),
        "description": metadata.get("description"),
        "tags": metadata.get("tags"),
        "chapters": metadata.get("chapters"),
        "num_speakers": task_options.get("num_speakers"),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


def _content_type(metadata: dict[str, Any], evidence: str) -> str:
    title = str(metadata.get("title") or "")
    subtype = str(metadata.get("content_subtype") or "")
    media_type = str(metadata.get("media_type") or "")
    if re.search(r"对谈|访谈|采访|interview", title, re.I):
        if "podcast" in subtype or media_type == "podcast":
            return "podcast_interview"
        return "interview"
    if "podcast" in subtype or media_type == "podcast":
        return "podcast"
    if re.search(r"会议|meeting", evidence, re.I):
        return "meeting"
    if re.search(r"教程|入门|tutorial", title, re.I):
        return "tutorial"
    return subtype or media_type or "unknown"


def _language_hint(evidence: str, metadata: dict[str, Any]) -> str:
    extra = metadata.get("extra") or {}
    configured = str(extra.get("detected_language") or extra.get("language") or "").strip()
    if configured and configured != "unknown":
        return configured
    chinese = len(re.findall(r"[\u4e00-\u9fff]", evidence))
    latin = len(re.findall(r"[A-Za-z]", evidence))
    if chinese >= max(8, latin // 3):
        return "zh-CN"
    return "en" if latin else "unknown"


def _timeline(metadata: dict[str, Any]) -> list[TimelineItem]:
    items: list[TimelineItem] = []
    chapters = metadata.get("chapters") or []
    for chapter in chapters:
        if not isinstance(chapter, dict):
            continue
        title = str(chapter.get("title") or "").strip()
        if not title:
            continue
        try:
            start = max(0.0, float(chapter.get("start_time") or 0))
        except (TypeError, ValueError):
            continue
        items.append(
            TimelineItem(
                start=round(start, 3),
                title=title,
                source="source_chapter",
                evidence="metadata.chapters",
            )
        )
    items.sort(key=lambda item: item.start)
    deduped: list[TimelineItem] = []
    for item in items:
        if deduped and item.start == deduped[-1].start:
            continue
        deduped.append(item)
    if deduped and deduped[0].start > 0:
        deduped.insert(
            0,
            TimelineItem(
                start=0,
                title="开场与嘉宾介绍",
                source="derived_opening_gap",
                evidence=f"first source chapter starts at {deduped[0].start:g}s",
            ),
        )
    return deduped


def _entity_type(value: str) -> str:
    lowered = value.lower()
    if lowered.endswith(" ai") or " lab" in lowered or " inc" in lowered:
        return "organization"
    if "bench" in lowered or "data" in lowered:
        return "product"
    if lowered == "rsi":
        return "term"
    if re.search(r"(?:model|gpt|kimi|llama|qwen|deepseek|rsi)", lowered):
        return "model"
    return "term"


def _deterministic_entities(evidence: str) -> list[SourceEntity]:
    entities: list[SourceEntity] = []
    seen: set[str] = set()
    for match in _LATIN_ENTITY_RE.finditer(evidence):
        value = re.sub(r"\s+", " ", match.group(0)).strip(" .,:;|｜")
        if len(value) < 2 or value.lower() in {
            "ai", "ceo", "cto", "host", "guest", "part", "the",
        }:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        entities.append(
            SourceEntity(
                canonical=value,
                type=_entity_type(value),
                evidence="source metadata",
                confidence=0.92,
            )
        )
    return entities[:80]


def _is_asr_hotword(entity: SourceEntity) -> bool:
    if entity.type in {"person", "organization", "product", "model", "paper"}:
        return True
    value = entity.canonical
    return bool(
        re.search(r"\d|[-.]", value)
        or re.search(r"(?:^|\s)[A-Z]{2,}(?:\s|$)", value)
    )


def _speaker_candidates(metadata: dict[str, Any], evidence: str) -> list[SpeakerCandidate]:
    candidates: list[SpeakerCandidate] = []
    seen: set[str] = set()

    def add(name: str, role: str, source: str, confidence: float) -> None:
        cleaned = name.strip()
        if cleaned in seen or not re.fullmatch(r"[\u4e00-\u9fff]{2,4}", cleaned):
            return
        seen.add(cleaned)
        candidates.append(
            SpeakerCandidate(
                name=cleaned,
                role=role,
                evidence=source,
                confidence=confidence,
            )
        )

    for role, pattern in _ROLE_PATTERNS:
        for match in pattern.finditer(evidence):
            add(match.group(1), role, "source metadata role label", 0.98)

    title = str(metadata.get("title") or "")
    guest_match = _TITLE_GUEST_RE.search(title)
    if guest_match:
        add(guest_match.group(1), "guest", "title interview guest", 0.96)
    return candidates


def _speaker_hint(
    metadata: dict[str, Any],
    task_options: dict[str, Any],
    candidates: list[SpeakerCandidate],
) -> SpeakerCountHint:
    user_value = task_options.get("num_speakers")
    if user_value is not None:
        try:
            count = int(user_value)
        except (TypeError, ValueError):
            count = 0
        if count > 0:
            return SpeakerCountHint(
                exact=count,
                confidence=1,
                evidence="task option num_speakers",
            )
    roles = {candidate.role for candidate in candidates}
    title = str(metadata.get("title") or "")
    if {"host", "guest"}.issubset(roles):
        return SpeakerCountHint(
            exact=len(candidates),
            confidence=0.97,
            evidence="source metadata names both host and guest",
        )
    if re.search(r"对谈|访谈|interview", title, re.I) and any(
        candidate.role == "guest" for candidate in candidates
    ):
        return SpeakerCountHint(
            exact=2,
            confidence=0.9,
            evidence="one-on-one interview title and named guest",
        )
    if re.search(r"对谈|访谈|interview", title, re.I):
        return SpeakerCountHint(
            min=2,
            max=4,
            confidence=0.7,
            evidence="interview-format title",
        )
    return SpeakerCountHint()


def build_deterministic_source_context(
    metadata: MediaMetadata | dict[str, Any],
    task_options: dict[str, Any] | None = None,
) -> SourceContext:
    data = _metadata_dict(metadata)
    options = dict(task_options or {})
    evidence = _source_evidence(data)
    speakers = _speaker_candidates(data, evidence)
    entities = _deterministic_entities(_entity_evidence(data))
    for candidate in speakers:
        if not any(entity.canonical == candidate.name for entity in entities):
            aliases = []
            short_name = candidate.name[1:] if len(candidate.name) == 3 else ""
            if short_name and short_name in evidence:
                aliases.append(short_name)
            entities.append(
                SourceEntity(
                    canonical=candidate.name,
                    aliases=aliases,
                    type="person",
                    evidence=candidate.evidence,
                    confidence=candidate.confidence,
                )
            )
    hotwords = _dedupe_strings(
        [entity.canonical for entity in entities if _is_asr_hotword(entity)]
        + [candidate.name for candidate in speakers]
    )
    return SourceContext(
        title=str(data.get("title") or ""),
        uploader=str(data.get("uploader") or ""),
        language_hint=_language_hint(evidence, data),
        content_type=_content_type(data, evidence),
        timeline=_timeline(data),
        entities=entities,
        speaker_candidates=speakers,
        speaker_count_hint=_speaker_hint(data, options, speakers),
        asr_hotwords=hotwords,
        source_signature=_signature(data, options),
    )


def _dedupe_strings(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = str(value or "").strip()
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        output.append(cleaned)
    return output


def _parse_json_object(response: str) -> dict[str, Any]:
    start = response.find("{")
    end = response.rfind("}") + 1
    if start < 0 or end <= start:
        raise ValueError("response does not contain a JSON object")
    value = json.loads(response[start:end])
    if not isinstance(value, dict):
        raise ValueError("response JSON is not an object")
    return value


def _evidence_contains(source_evidence: str, entity: SourceEntity) -> bool:
    folded = source_evidence.casefold()
    return entity.canonical.casefold() in folded or any(
        alias.casefold() in folded for alias in entity.aliases if alias.strip()
    )


def _merge_patch(
    baseline: SourceContext,
    patch: SourceContextPatch,
    source_evidence: str,
) -> SourceContext:
    entities = list(baseline.entities)
    by_name = {item.canonical.casefold(): item for item in entities}
    for item in patch.entities:
        if not _evidence_contains(source_evidence, item):
            continue
        key = item.canonical.casefold()
        existing = by_name.get(key)
        if existing:
            existing.aliases = _dedupe_strings(existing.aliases + item.aliases)
            existing.confidence = max(existing.confidence, item.confidence)
        else:
            entities.append(item)
            by_name[key] = item

    speakers = list(baseline.speaker_candidates)
    seen_speakers = {item.name for item in speakers}
    for item in patch.speaker_candidates:
        if item.name not in source_evidence or item.name in seen_speakers:
            continue
        speakers.append(item)
        seen_speakers.add(item.name)

    hint = baseline.speaker_count_hint
    if hint.confidence < 0.85 and patch.speaker_count_hint.confidence > hint.confidence:
        hint = patch.speaker_count_hint

    hotwords = _dedupe_strings(
        baseline.asr_hotwords
        + [item.canonical for item in entities if _is_asr_hotword(item)]
        + [item.name for item in speakers]
    )
    return baseline.model_copy(
        update={
            "language_hint": (
                patch.language_hint
                if baseline.language_hint == "unknown" and patch.language_hint != "unknown"
                else baseline.language_hint
            ),
            "content_type": (
                patch.content_type
                if baseline.content_type == "unknown" and patch.content_type != "unknown"
                else baseline.content_type
            ),
            "entities": entities,
            "speaker_candidates": speakers,
            "speaker_count_hint": hint,
            "asr_hotwords": hotwords,
        }
    )


async def _tag_with_llm(
    metadata: dict[str, Any],
    baseline: SourceContext,
) -> SourceContextPatch:
    from app.services.analysis.llm import get_llm_service

    source_payload = {
        "title": metadata.get("title"),
        "uploader": metadata.get("uploader"),
        "description": str(metadata.get("description") or "")[:8000],
        "tags": (metadata.get("tags") or [])[:50],
        "chapters": metadata.get("chapters") or [],
    }
    prompt = get_source_context_prompt(source_payload, baseline.model_dump(mode="json"))
    service = get_llm_service()
    response = await service._call(
        prompt,
        stage="analyze",
        system_prompt=SOURCE_CONTEXT_SYSTEM_PROMPT,
    )
    try:
        return SourceContextPatch.model_validate(_parse_json_object(response))
    except (ValueError, json.JSONDecodeError, ValidationError) as first_error:
        repair_prompt = get_source_context_repair_prompt(
            prompt,
            response,
            str(first_error),
        )
        repaired = await service._call(
            repair_prompt,
            stage="analyze",
            system_prompt=SOURCE_CONTEXT_SYSTEM_PROMPT,
        )
        return SourceContextPatch.model_validate(_parse_json_object(repaired))


async def build_source_context(
    metadata: MediaMetadata | dict[str, Any],
    task_options: dict[str, Any] | None = None,
    *,
    enrich: bool = True,
) -> SourceContext:
    data = _metadata_dict(metadata)
    baseline = build_deterministic_source_context(data, task_options)
    if not enrich:
        return baseline
    try:
        patch = await _tag_with_llm(data, baseline)
        merged = _merge_patch(baseline, patch, _source_evidence(data))
        log_event(
            logger,
            logging.INFO,
            "source_context.tagged",
            entities=len(merged.entities),
            speakers=len(merged.speaker_candidates),
            timeline=len(merged.timeline),
        )
        return merged
    except Exception as exc:
        log_event(
            logger,
            logging.WARNING,
            "source_context.tagger_fallback",
            error=exc,
        )
        return baseline


async def load_or_build_source_context(
    metadata: MediaMetadata | dict[str, Any],
    task_options: dict[str, Any] | None,
    context_path: str | Path,
    *,
    enrich: bool = True,
) -> SourceContext:
    path = Path(context_path)
    data = _metadata_dict(metadata)
    expected_signature = _signature(data, dict(task_options or {}))
    if path.is_file():
        try:
            cached = SourceContext.model_validate_json(path.read_text(encoding="utf-8"))
            if cached.source_signature == expected_signature:
                return cached
        except (OSError, ValidationError, ValueError):
            log_event(logger, logging.WARNING, "source_context.cache_invalid", path=path)
    context = await build_source_context(data, task_options, enrich=enrich)
    return context


def source_context_to_analysis(context: SourceContext | dict[str, Any]) -> dict[str, Any]:
    value = context if isinstance(context, SourceContext) else SourceContext.model_validate(context)
    return {
        "language": value.language_hint,
        "content_type": value.content_type,
        "main_topics": [item.title for item in value.timeline],
        "keywords": value.asr_hotwords,
        "proper_nouns": [item.canonical for item in value.entities],
        "entities": [item.model_dump(mode="json") for item in value.entities],
        "timeline": [item.model_dump(mode="json") for item in value.timeline],
        "speaker_candidates": [
            item.model_dump(mode="json") for item in value.speaker_candidates
        ],
        "speaker_count_hint": value.speaker_count_hint.model_dump(mode="json"),
    }


def merge_analysis_with_source(
    analysis: dict[str, Any] | None,
    context: SourceContext | dict[str, Any],
) -> dict[str, Any]:
    source = source_context_to_analysis(context)
    result = dict(analysis or {})
    if not result.get("language") or result.get("language") == "unknown":
        result["language"] = source["language"]
    if not result.get("content_type") or result.get("content_type") == "unknown":
        result["content_type"] = source["content_type"]
    for key in ("main_topics", "keywords", "proper_nouns"):
        result[key] = _dedupe_strings(list(result.get(key) or []) + list(source.get(key) or []))
    for key in ("entities", "timeline", "speaker_candidates", "speaker_count_hint"):
        result[key] = source[key]
    exact = source["speaker_count_hint"].get("exact")
    if exact:
        result["speakers_detected"] = exact
    return canonicalize_json(result, context)


def speaker_constraints(
    context: SourceContext | dict[str, Any] | None,
    task_options: dict[str, Any] | None = None,
) -> tuple[int | None, int | None, int | None]:
    options = dict(task_options or {})
    if options.get("num_speakers") is not None:
        try:
            exact = int(options["num_speakers"])
        except (TypeError, ValueError):
            exact = 0
        if exact > 0:
            return exact, exact, exact
    if context is None:
        return None, None, None
    value = context if isinstance(context, SourceContext) else SourceContext.model_validate(context)
    hint = value.speaker_count_hint
    if hint.exact and hint.confidence >= 0.85:
        return hint.exact, hint.exact, hint.exact
    if hint.confidence >= 0.65:
        return None, hint.min, hint.max
    return None, None, None


def merge_hotwords(
    explicit: list[str] | None,
    context: SourceContext | dict[str, Any] | None,
) -> list[str]:
    values = list(explicit or [])
    if context is not None:
        value = (
            context
            if isinstance(context, SourceContext)
            else SourceContext.model_validate(context)
        )
        values.extend(value.asr_hotwords)
    return _dedupe_strings(values)


def canonicalize_text(text: str, context: SourceContext | dict[str, Any] | None) -> str:
    if not text or context is None:
        return text
    raw_entities = (
        context.entities
        if isinstance(context, SourceContext)
        else context.get("entities") or []
    )
    output = text
    replacements: list[tuple[str, str]] = []
    entities: list[SourceEntity] = []
    for raw_entity in raw_entities:
        entity = (
            raw_entity
            if isinstance(raw_entity, SourceEntity)
            else SourceEntity.model_validate(raw_entity)
        )
        entities.append(entity)
        replacements.extend(
            (alias, entity.canonical)
            for alias in entity.aliases
            if alias and alias.casefold() != entity.canonical.casefold()
        )
    placeholders: dict[str, str] = {}
    for index, canonical in enumerate(
        sorted({item.canonical for item in entities}, key=len, reverse=True)
    ):
        placeholder = f"\uFFF0{index}\uFFF1"
        canonical_pattern = re.escape(canonical)
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9.-]*", canonical):
            canonical_pattern = (
                rf"\b{canonical_pattern}\b(?!\s+[A-Za-z][A-Za-z0-9.-]*)"
            )
        elif re.fullmatch(
            r"[A-Za-z][A-Za-z0-9.-]*(?:\s+[A-Za-z][A-Za-z0-9.-]*)+",
            canonical,
        ):
            canonical_pattern = rf"\b{canonical_pattern}\b"
        replaced, count = re.subn(
            canonical_pattern,
            placeholder,
            output,
            flags=re.IGNORECASE,
        )
        if count:
            output = replaced
            placeholders[placeholder] = canonical
    for alias, canonical in sorted(replacements, key=lambda item: len(item[0]), reverse=True):
        output = re.sub(re.escape(alias), canonical, output, flags=re.IGNORECASE)

    for entity in entities:
        canonical = entity.canonical
        if entity.type not in {"person", "organization", "product", "model", "paper"}:
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]{3,4}", canonical) and entity.type == "person":
            chinese_window = rf"(?=([\u4e00-\u9fff]{{{len(canonical)}}}))"
            for candidate in set(re.findall(chinese_window, output)):
                if (
                    len(candidate) == len(canonical)
                    and candidate[0] == canonical[0]
                    and candidate[-1] == canonical[-1]
                    and sum(a != b for a, b in zip(candidate, canonical, strict=True)) == 1
                ):
                    output = output.replace(candidate, canonical)
            continue
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9.-]*(?:\s+[A-Za-z][A-Za-z0-9.-]*)*", canonical):
            continue
        word_count = len(canonical.split())
        if len(canonical) < 7:
            continue
        pattern = r"\b[A-Za-z][A-Za-z0-9.-]*"
        if word_count > 1:
            pattern += (r"\s+[A-Za-z][A-Za-z0-9.-]*") * (word_count - 1)
        for candidate in set(re.findall(f"(?=({pattern}))", output)):
            if candidate.casefold() == canonical.casefold():
                continue
            similarity = SequenceMatcher(
                None,
                candidate.casefold().replace(" ", ""),
                canonical.casefold().replace(" ", ""),
            ).ratio()
            if similarity >= 0.82:
                output = re.sub(re.escape(candidate), canonical, output, flags=re.IGNORECASE)
    for placeholder, canonical in placeholders.items():
        output = output.replace(placeholder, canonical)
    return output


def canonicalize_json(value: Any, context: SourceContext | dict[str, Any] | None) -> Any:
    if isinstance(value, str):
        return canonicalize_text(value, context)
    if isinstance(value, list):
        return [canonicalize_json(item, context) for item in value]
    if isinstance(value, dict):
        return {key: canonicalize_json(item, context) for key, item in value.items()}
    return value
