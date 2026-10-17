"""Evaluate synthetic regression cases; these are not a population-quality benchmark."""

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.analysis.content_selection import (  # noqa: E402
    CONTENT_SELECTION_RESPONSE_SCHEMA,
    CONTENT_SELECTION_SYSTEM_PROMPT,
)
from app.services.analysis.local_llm_runtime import release_local_llm_runtime  # noqa: E402
from app.services.analysis.outline_organization import (  # noqa: E402
    GROUPING_SCHEMA,
    organize_outline,
)
from app.services.analysis.summary_source import prepare_summary_source  # noqa: E402
from evaluate_summary_structure import MeasuredCalls, save_json  # noqa: E402


async def evaluate(args):
    cases = json.loads(Path(args.cases).read_text(encoding="utf-8-sig"))["cases"]
    metrics = []
    for case in cases:
        directory = Path(args.output) / case["id"]
        directory.mkdir(parents=True, exist_ok=True)
        calls = MeasuredCalls(args.provider, directory)
        calls.schema = CONTENT_SELECTION_RESPONSE_SCHEMA
        calls.stage = "analyze"

        async def select_call(prompt):
            return await calls(prompt, system_prompt=CONTENT_SELECTION_SYSTEM_PROMPT)

        started = time.perf_counter()
        prepared = await prepare_summary_source(
            case["source_text"],
            {
                "title": case["title"],
                "content_type": case["content_type"],
            },
            {},
            select_call,
        )
        calls.schema = GROUPING_SCHEMA
        calls.stage = "mindmap"
        # Fixtures use H1 for each flat topic; production outlines use list/H2 markers.
        markdown = re.sub(r"(?m)^# ", "- ", case["expected"]["tree_markdown"])
        result, report = await organize_outline(markdown, calls, language="zh-CN", budget=10000)
        (directory / "mindmap.md").write_text(result, encoding="utf-8")
        (directory / "selected-source.txt").write_text(prepared["text"], encoding="utf-8")
        metric = {
            "id": case["id"],
            "synthetic": True,
            "provider": args.provider,
            "seconds": round(time.perf_counter() - started, 3),
            "calls": len(calls.calls),
            "expected_has_preview": case["has_preview"],
            "selection": prepared["selection"],
            "organization": report,
        }
        metrics.append(metric)
        save_json(directory / "metrics.json", metric)
        save_json(Path(args.output) / "metrics.json", metrics)
        print(f"Finished synthetic case {case['id']}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        asyncio.run(evaluate(args))
    finally:
        release_local_llm_runtime()


if __name__ == "__main__":
    main()
