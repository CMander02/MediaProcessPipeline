"""Share the selected source and its chapter anchors across summary outputs."""

from __future__ import annotations

from app.services.analysis.content_selection import select_main_content
from app.services.analysis.mindmap_outputs import mindmap_markdown_to_timed_tree
from app.services.analysis.outline_organization import outline_blocks
from app.services.analysis.transcript_outputs import _parse_srt, _timestamp_to_seconds


def outline_guided_context(source_context: dict, mindmap: str) -> dict:
    """Use the global organization as a guide while the transcript remains evidence."""
    chapter_points = {}

    def visit(node):
        if node.get("start") is not None and node.get("children"):
            chapter_points[(node["title"], float(node["start"]))] = "；".join(
                child["title"] for child in node["children"]
            )
        for child in node.get("children", []):
            visit(child)

    tree = mindmap_markdown_to_timed_tree(mindmap)
    visit(tree)
    timeline = list(source_context.get("timeline") or [])
    if not timeline:
        # Recordings often have no publisher chapters. The organized outline
        # supplies semantic sections; their anchors still come from source cues.
        def timed_points(node):
            children = node.get("children") or []
            if not children:
                return [node] if node.get("start") is not None else []
            return [point for child in children for point in timed_points(child)]

        sections = tree.get("children", [])
        # Some short-content responses include a document-title wrapper. Its
        # semantic children should still have separate navigable summaries.
        if len(sections) == 1:
            children = sections[0].get("children") or []
            if len(children) > 1 and any(child.get("children") for child in children):
                sections = children
        for section in sections:
            points = timed_points(section)
            if not points:
                continue
            timeline.append({
                "start": min(float(point["start"]) for point in points),
                "title": section["title"],
                "summary": "；".join(point["title"] for point in points),
                "source": "organized_outline",
            })
        timeline.sort(key=lambda item: float(item["start"]))
    return {
        **source_context,
        "topic_outline": [block.splitlines()[0][2:] for block in outline_blocks(mindmap)],
        "timeline": [
            {**item, "summary": chapter_points.get(
                (item["title"], float(item["start"])), item.get("summary", "")
            )}
            for item in timeline
        ],
    }


async def prepare_summary_source(text: str, metadata: dict, source_context: dict, call) -> dict:
    # A short standalone utterance has no separate teaser/body to disambiguate.
    if len(text.strip()) < 400:
        return {
            "text": text,
            "source_context": dict(source_context),
            "selection": {"status": "unchanged", "reason": "short_source", "excluded_ids": []},
        }
    selection = await select_main_content(
        text,
        {**metadata, "timeline": source_context.get("timeline", [])},
        call,
    )
    filtered = selection["filtered_text"]
    context = dict(source_context)
    if selection["report"]["status"] == "filtered":
        timeline = list(context.get("timeline") or [])
        segments = _parse_srt(filtered)
        if not segments:
            # Plain text has no reliable cue-to-chapter mapping.  Retaining
            # the old timeline here would attach teaser anchors to the newly
            # filtered body, so clear it for downstream map/summary stages.
            context["timeline"] = []
        elif timeline:
            starts = [
                _timestamp_to_seconds(segment["timestamp"].split("-->")[0].strip())
                for segment in segments
            ]
            context["timeline"] = [
                item
                for index, item in enumerate(timeline)
                if any(
                    start is not None
                    and float(item["start"])
                    <= start
                    < (
                        float(timeline[index + 1]["start"])
                        if index + 1 < len(timeline)
                        else float("inf")
                    )
                    for start in starts
                )
            ]
            # A teaser can end before the publisher's next chapter boundary.
            # Keep the real host introduction that remains there, with a derived
            # label/anchor, instead of retaining a now-misleading teaser title.
            if context["timeline"]:
                first = context["timeline"][0]
                remaining_start = min(start for start in starts if start is not None)
                excluded = selection["report"].get("excluded_ranges") or []
                if excluded and float(first["start"]) < max(span["end"] for span in excluded):
                    context["timeline"][0] = {
                        **first, "start": remaining_start, "title": "正文开场",
                        "source": "selected_opening", "source_start": first["start"],
                        "evidence": "content_selection: retained opening after the teaser",
                    }
    return {"text": filtered, "source_context": context, "selection": selection["report"]}
