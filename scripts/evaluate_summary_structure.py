"""Run identical outline-organization inputs through registered model providers.

Writes evaluation artifacts only. Credentials stay in the existing model router.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.model_router import resolve_llm_binding  # noqa: E402
from app.core.settings import get_runtime_settings  # noqa: E402
from app.services.analysis.llm import LLMService  # noqa: E402
from app.services.analysis.local_llm_runtime import release_local_llm_runtime  # noqa: E402
from app.services.analysis.outline_organization import (  # noqa: E402
    GROUPING_SCHEMA,
    organize_outline,
)
from app.services.analysis.transcript_outputs import _seconds_to_srt_timestamp  # noqa: E402


def save_json(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


class UsageHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.usages = []

    def emit(self, record):
        message = record.getMessage()
        if "event=llm.local.call_completed" in message:
            self.usages.append(
                {
                    key: int(value)
                    for key, value in re.findall(
                        r"(prompt_tokens|completion_tokens)=(\d+)",
                        message,
                    )
                }
            )


def tree_markdown(tree: dict) -> str:
    lines = []

    def visit(node, depth):
        label = node["title"]
        if node.get("start") is not None:
            label += " [" + _seconds_to_srt_timestamp(node["start"]).replace(",", ".")
            if node.get("end") is not None:
                label += " - " + _seconds_to_srt_timestamp(node["end"]).replace(",", ".")
            label += "]"
        lines.append("  " * depth + "- " + label)
        for child in node.get("children", []):
            visit(child, depth + 1)

    for child in tree.get("children", []):
        visit(child, 0)
    return "\n".join(lines)


class MeasuredCalls:
    def __init__(self, provider: str, directory: Path):
        self.provider = provider
        self.directory = directory
        self.service = LLMService()
        self.raw_call = self.service._call
        self.calls = []
        self.stage = "mindmap"
        self.schema = GROUPING_SCHEMA
        self.usage = UsageHandler()
        logger = logging.getLogger("app.services.analysis.llm")
        logger.setLevel(logging.INFO)
        logger.addHandler(self.usage)

    async def __call__(self, prompt: str, *, system_prompt: str = "", **kwargs) -> str:
        started = time.perf_counter()
        stage = kwargs.get("stage") or self.stage
        usage_before = len(self.usage.usages)
        response = await self.raw_call(
            prompt,
            provider_override=self.provider,
            stage=stage,
            system_prompt=system_prompt,
            response_schema=kwargs.get("response_schema", self.schema),
        )
        record = {
            "stage": stage,
            "seconds": round(time.perf_counter() - started, 3),
            "prompt_bytes": len((system_prompt + prompt).encode("utf-8")),
            "response_chars": len(response),
            "prompt": prompt,
            "system_prompt": system_prompt,
            "response": response,
            "usage": self.usage.usages[usage_before:],
        }
        self.calls.append(record)
        (self.directory / "calls.json").write_text(
            json.dumps(self.calls, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"{self.provider}: call {len(self.calls)} {record['seconds']}s", flush=True)
        return response


async def evaluate(args):
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    calls = MeasuredCalls(args.provider, output)
    binding = resolve_llm_binding(get_runtime_settings(), args.provider, "mindmap")
    source = Path(args.archive)
    started = time.perf_counter()
    report = {}
    if args.mode in {"selection", "full"}:
        from app.services.analysis.content_selection import (
            CONTENT_SELECTION_RESPONSE_SCHEMA,
            CONTENT_SELECTION_SYSTEM_PROMPT,
        )
        from app.services.analysis.source_context import build_deterministic_source_context
        from app.services.analysis.summary_source import (
            outline_guided_context,
            prepare_summary_source,
        )

        metadata = json.loads((source / "metadata.json").read_text(encoding="utf-8"))
        context = build_deterministic_source_context(metadata).model_dump(mode="json")
        text = (source / "transcript_polished.srt").read_text(encoding="utf-8")
        calls.schema = CONTENT_SELECTION_RESPONSE_SCHEMA
        calls.stage = "analyze"

        async def select_call(prompt):
            return await calls(prompt, system_prompt=CONTENT_SELECTION_SYSTEM_PROMPT)

        prepared = await prepare_summary_source(text, metadata, context, select_call)
        report["selection"] = prepared["selection"]
        save_json(output / "content_selection.json", prepared["selection"])
        save_json(output / "source_context.json", context)
        save_json(output / "selected_context.json", prepared["source_context"])
        (output / "selected.srt").write_text(prepared["text"], encoding="utf-8")
        if args.mode == "full":
            calls.schema = None
            calls.service._call = calls
            flat = None
            if args.reuse_outline:
                flat = Path(args.reuse_outline).read_text(encoding="utf-8")
            else:
                # Save the common extraction independently of global organization.
                async def save_flat(markdown, language, provider):
                    (output / "mindmap-flat.md").write_text(markdown, encoding="utf-8")
                    return markdown

                calls.service._organize_mindmap = save_flat
                flat = await calls.service.mindmap(
                    prepared["text"],
                    metadata={
                        **metadata,
                        "source_context": prepared["source_context"],
                        "chapters": [
                            {"title": item["title"], "start_time": item["start"]}
                            for item in prepared["source_context"]["timeline"]
                        ],
                    },
                    user_language="zh-CN",
                    provider_override=args.provider,
                )
            calls.schema = GROUPING_SCHEMA
            calls.stage = "mindmap"
            result, report["organization"] = await organize_outline(
                flat,
                calls,
                language="zh-CN",
                budget=10000,
            )
            calls.schema = None
            from app.core.pipeline_steps.transcript import _plain_text_from_srt

            summary = await calls.service.summarize(
                _plain_text_from_srt(prepared["text"]),
                user_language="zh-CN",
                provider_override=args.provider,
                source_context=outline_guided_context(prepared["source_context"], result),
            )
            save_json(output / "summary.json", summary)
            from app.services.analysis.mindmap_outputs import (
                mindmap_markdown_to_timed_tree,
                mindmap_markdown_without_timestamps,
            )

            save_json(output / "mindmap.json", mindmap_markdown_to_timed_tree(result))
            (output / "mindmap-timed.md").write_text(result, encoding="utf-8")
            (output / "mindmap.md").write_text(
                mindmap_markdown_without_timestamps(result),
                encoding="utf-8",
            )
    else:
        tree = json.loads((source / "mindmap.json").read_text(encoding="utf-8"))
        markdown = tree_markdown(tree)
        result, report = await organize_outline(markdown, calls, language="zh-CN", budget=10000)
        (output / "mindmap.md").write_text(result, encoding="utf-8")
    metrics = {
        "provider": args.provider,
        "model": binding.model,
        "reasoning_effort": binding.request_kwargs.get("reasoning_effort"),
        "context": binding.request_kwargs.get("ctx"),
        "mode": args.mode,
        "input": str(source),
        "reused_outline": args.reuse_outline,
        "seconds": round(time.perf_counter() - started, 3),
        "calls": len(calls.calls),
        "organization": report,
    }
    (output / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(metrics, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--mode", choices=["structure", "selection", "full"], default="structure")
    parser.add_argument("--reuse-outline", help="Shared extraction; only classify and summarize it")
    args = parser.parse_args()
    try:
        asyncio.run(evaluate(args))
    finally:
        release_local_llm_runtime()


if __name__ == "__main__":
    main()
