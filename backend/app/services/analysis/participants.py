"""People a source names as taking part in it: title collaborations, the uploader, listed guests."""

from __future__ import annotations

import re
from typing import Any

# A separator that marks a collaboration on its own: 【A×B】, A & B, A 对谈 B, 瓜熟迪落拉XEric.
_STRONG_SEPARATOR = re.compile(
    r"\s*(?:[×✕✖＆&]|\bvs\.?(?=\s)|\bVS\b|对谈|对话)\s*"
    r"|\s+[xX]\s+"
    r"|(?<=[一-鿿])[xX](?=[一-鿿A-Z])"
)
# 和 / 与 join ordinary nouns too, so they only count when one side is the uploader.
_WEAK_SEPARATOR = re.compile(r"\s*[和与]\s*")
_BRACKETS = re.compile(r"【([^】]{2,80})】|\[([^\]]{2,80})\]|「([^」]{2,80})」|〖([^〗]{2,80})〗")
_CHUNKS = re.compile(r"\s*(?:[｜|：:]|\s[-–—]\s)\s*")
_EPISODE = re.compile(r"(?:\s*(?:第\s*\d+\s*[期集话回]|[Ee][Pp]\.?\s*\d+|#\s*\d+|[（(]\s*[上中下]\s*[)）])|\d{1,3})$")
_GENERIC = {
    "访谈", "对谈", "对话", "播客", "完整版", "中字", "中英字幕", "熟肉", "生肉", "合集", "精华", "直播", "录播",
    "字幕", "官方", "嘉宾", "主持", "专访", "圆桌", "预告", "番外", "现场", "全程", "高清", "podcast", "interview",
    "live", "full", "ep", "official",
}


def _clean(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" \t-–—·•,，、.。!！?？\"'“”‘’()（）")
    # Episode numbers glued to the last name: "Eric Hovagim02" is Eric Hovagim, episode 02.
    previous = None
    while previous != text:
        previous = text
        text = _EPISODE.sub("", text).strip()
    return text


def _name_like(value: str) -> bool:
    if not value or value.casefold() in _GENERIC or len(value) > 40:
        return False
    if re.fullmatch(r"[一-鿿·]{2,8}", value):
        return True
    words = value.split(" ")
    if len(words) > 4:
        return False
    # Latin names, optionally mixed with CJK handles such as "X博士" or "Tim 老师".
    return all(re.fullmatch(r"[A-Za-z一-鿿][\w.'’一-鿿-]*", word) for word in words) \
        and bool(re.search(r"[A-Za-z一-鿿]{2,}", value))


def same_person_name(left: str, right: str) -> bool:
    def key(value):
        return re.sub(r"\s+", "", str(value or "")).casefold()
    return bool(key(left)) and key(left) == key(right)


def title_collaborators(title: str, uploader: str = "") -> list[str]:
    """Names a title presents as a collaboration, in title order."""
    title = str(title or "")
    segments = [next(group for group in match.groups() if group) for match in _BRACKETS.finditer(title)]
    segments += [chunk for chunk in _CHUNKS.split(_BRACKETS.sub(" ", title)) if chunk.strip()]
    for segment in segments:
        for separator, needs_uploader in ((_STRONG_SEPARATOR, False), (_WEAK_SEPARATOR, True)):
            parts = [_clean(part) for part in separator.split(segment)]
            parts = [part for part in parts if part]
            if len(parts) < 2 or len(parts) > 4 or not all(_name_like(part) for part in parts):
                continue
            if needs_uploader and not any(same_person_name(part, uploader) for part in parts):
                continue
            names: list[str] = []
            for part in parts:
                if not any(same_person_name(part, name) for name in names):
                    names.append(part)
            if len(names) >= 2:
                return names
    return []


def source_participants(context: dict[str, Any] | None) -> list[dict[str, str]]:
    """People the source says take part, with a role and where that came from.

    A name here is someone likely to be heard, not proof of which voice is theirs.
    """
    context = context or {}
    rows: list[dict[str, str]] = []

    def add(name: Any, role: str, basis: str) -> None:
        name = _clean(str(name or ""))
        if not name or any(char in name for char in "[]<>\n\r") or any(same_person_name(name, row["name"]) for row in rows):
            return
        rows.append({"name": name, "role": role, "basis": basis})

    uploader = str(context.get("uploader") or "").strip()
    for name in title_collaborators(str(context.get("title") or ""), uploader):
        host = bool(uploader) and same_person_name(name, uploader)
        add(name, "host" if host else "guest", "标题联名，且是上传者" if host else "标题联名")
    for candidate in context.get("speaker_candidates") or []:
        if isinstance(candidate, dict) and candidate.get("role") in {"host", "guest", "speaker"}:
            add(candidate.get("name"), str(candidate["role"]), f"来源资料（{candidate.get('evidence') or '候选人'}）")
    evidence = context.get("participant_evidence") or {}
    for person in evidence.get("participants") or [] if isinstance(evidence, dict) else []:
        if isinstance(person, dict) and person.get("name"):
            add(person["name"], str(person.get("role") or "participant"), "画面或资料中的姓名")
    return rows
