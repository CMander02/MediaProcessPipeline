"""Zhihu content extraction via Playwright.

Zhihu blocks plain HTTP and often blocks headless browsers for answer pages.
The page still embeds the useful normalized state in ``#js-initialData`` when
loaded in a real browser, so this module uses Playwright with a headed fallback
and then parses that initial state instead of scraping rendered DOM text.
"""

from __future__ import annotations

import html
import json
import logging
import re
import urllib.request
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from app.core.network import urllib_urlopen
from app.core.settings import get_runtime_settings

logger = logging.getLogger(__name__)

_PIN_RE = re.compile(r"zhihu\.com/pin/(\d+)", re.IGNORECASE)
_ANSWER_RE = re.compile(r"zhihu\.com/question/(\d+)/answer/(\d+)", re.IGNORECASE)
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


def is_zhihu_url(value: str) -> bool:
    return bool(_PIN_RE.search(value) or _ANSWER_RE.search(value))


def fetch_metadata(url: str) -> dict[str, Any]:
    """Fetch and normalize metadata for a Zhihu pin or answer URL."""
    if not is_zhihu_url(url):
        raise RuntimeError(f"Unsupported Zhihu URL: {url}")

    errors: list[str] = []
    for headless in (True, False):
        try:
            state = _fetch_initial_state_once(url, headless=headless)
            if _PIN_RE.search(url):
                return _extract_pin(url, state)
            return _extract_answer(url, state)
        except Exception as e:
            mode = "headless" if headless else "headed"
            errors.append(f"{mode}: {e}")
            logger.warning("Zhihu metadata fetch failed (%s): %s", mode, e)
    raise RuntimeError("Zhihu metadata not found. " + " | ".join(errors))


def download_images(info: dict[str, Any], output_dir: Path) -> list[Path]:
    """Download embedded Zhihu images when present."""
    image_urls: list[str] = (info.get("extra") or {}).get("image_urls") or []
    if not image_urls:
        raise RuntimeError("Zhihu content has no image URLs")

    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    referer = str(info.get("webpage_url") or "https://www.zhihu.com/")
    paths: list[Path] = []
    for idx, url in enumerate(image_urls):
        dest = images_dir / f"{idx:02d}.{_guess_image_ext(url)}"
        if dest.exists():
            paths.append(dest)
            continue
        req = urllib.request.Request(url, headers=_headers(referer))
        with urllib_urlopen(req, timeout=30) as resp:
            dest.write_bytes(resp.read())
        paths.append(dest)
    return paths


def _fetch_initial_state(url: str) -> dict[str, Any]:
    errors: list[str] = []
    for headless in (True, False):
        try:
            state = _fetch_initial_state_once(url, headless=headless)
            if state:
                return state
        except Exception as e:
            errors.append(f"{'headless' if headless else 'headed'}: {e}")
            logger.warning("Zhihu Playwright fetch failed (%s): %s", "headless" if headless else "headed", e)
    raise RuntimeError("Zhihu initial state not found. " + " | ".join(errors))


def _fetch_initial_state_once(url: str, *, headless: bool) -> dict[str, Any]:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            "Playwright is not installed. Run `uv add playwright` and "
            "`uv run playwright install chromium`."
        ) from e

    with sync_playwright() as p:
        launch_args = ["--disable-blink-features=AutomationControlled"]
        if not headless and get_runtime_settings().zhihu_browser_mode == "background":
            launch_args.append("--start-minimized")
        browser = p.chromium.launch(
            headless=headless,
            args=launch_args,
        )
        try:
            context = browser.new_context(
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                viewport={"width": 1365, "height": 900},
                user_agent=_UA,
            )
            page = context.new_page()
            page.goto(url, wait_until="commit", timeout=45_000)
            last: dict[str, Any] | None = None
            for _ in range(12):
                page.wait_for_timeout(1_000)
                try:
                    last = page.evaluate(
                        """() => ({
                            title: document.title,
                            initial: document.querySelector('#js-initialData')?.textContent || '',
                            body: (document.body?.innerText || '').slice(0, 500)
                        })"""
                    )
                except (PlaywrightError, PlaywrightTimeoutError):
                    continue
                initial = str((last or {}).get("initial") or "")
                if len(initial) > 1000:
                    data = json.loads(initial)
                    state = data.get("initialState") or data.get("state") or data
                    if isinstance(state, dict):
                        return state
            body = str((last or {}).get("body") or "")
            raise RuntimeError(f"initial data missing; body starts with: {body[:160]}")
        finally:
            browser.close()


def _extract_pin(url: str, state: dict[str, Any]) -> dict[str, Any]:
    pin_id = _PIN_RE.search(url).group(1)  # type: ignore[union-attr]
    pins = _nested_dict(state, "entities", "pins")
    pin = pins.get(pin_id) if isinstance(pins, dict) else None
    if not isinstance(pin, dict) and isinstance(pins, dict) and pins:
        pin = next((v for v in pins.values() if isinstance(v, dict)), None)
    if not isinstance(pin, dict):
        raise RuntimeError(f"Zhihu pin data not found: {pin_id}")

    content_html = (
        pin.get("contentHtml")
        or pin.get("excerptTitle")
        or _join_content_array(pin.get("content"))
        or pin.get("content")
        or ""
    )
    text = _html_to_text(str(content_html))
    title = _first_non_empty(
        _html_to_text(str(pin.get("excerptTitle") or "")),
        text.splitlines()[0] if text else "",
        f"Zhihu pin {pin_id}",
    )
    author_ref = pin.get("author") or pin.get("user")
    author_obj = _resolve_user(state, author_ref)
    author = _author_name(author_obj or author_ref)
    created = _int_or_none(pin.get("created") or pin.get("createdTime"))

    return {
        "id": pin_id,
        "title": _short_title(title),
        "description": text,
        "uploader": author,
        "uploader_id": _author_id(author_obj or author_ref),
        "platform": "zhihu",
        "content_subtype": "image_note" if _image_urls(pin) else "text_note",
        "duration": None,
        "upload_date": _date_yyyymmdd(created),
        "timestamp": created,
        "webpage_url": url,
        "original_url": url,
        "thumbnail": _first_image(pin),
        "media_type": "image" if _image_urls(pin) else "other",
        "tags": ["知乎", "想法"],
        "extra": {
            "platform": "zhihu",
            "zhihu_type": "pin",
            "pin_id": pin_id,
            "like_count": pin.get("likeCount"),
            "comment_count": pin.get("commentCount"),
            "image_urls": _image_urls(pin),
        },
    }


def _extract_answer(url: str, state: dict[str, Any]) -> dict[str, Any]:
    match = _ANSWER_RE.search(url)
    question_id, answer_id = match.group(1), match.group(2)  # type: ignore[union-attr]
    questions = _nested_dict(state, "entities", "questions")
    answers = _nested_dict(state, "entities", "answers")
    question = questions.get(question_id) if isinstance(questions, dict) else None
    answer = answers.get(answer_id) if isinstance(answers, dict) else None
    if not isinstance(answer, dict) and isinstance(answers, dict) and answers:
        answer = next((v for v in answers.values() if isinstance(v, dict)), None)
    if not isinstance(answer, dict):
        raise RuntimeError(f"Zhihu answer data not found: {answer_id}")
    if not isinstance(question, dict):
        question = {}

    answer_text = _html_to_text(str(answer.get("content") or ""))
    question_title = str(question.get("title") or answer.get("question", {}).get("title") or "知乎回答")
    question_detail = _html_to_text(str(question.get("detail") or question.get("excerpt") or ""))
    author = _author_name(answer.get("author"))
    created = _int_or_none(answer.get("createdTime") or answer.get("created"))

    parts = [f"# {question_title}"]
    if question_detail:
        parts.append(question_detail)
    if author:
        parts.append(f"回答者：{author}")
    if answer_text:
        parts.append(answer_text)

    return {
        "id": answer_id,
        "title": question_title,
        "description": "\n\n".join(parts),
        "uploader": author,
        "uploader_id": _author_id(answer.get("author")),
        "platform": "zhihu",
        "content_subtype": "image_note" if _image_urls(answer) else "text_note",
        "duration": None,
        "upload_date": _date_yyyymmdd(created),
        "timestamp": created,
        "webpage_url": url,
        "original_url": url,
        "thumbnail": _first_image(answer),
        "media_type": "image" if _image_urls(answer) else "other",
        "tags": ["知乎", "回答"],
        "extra": {
            "platform": "zhihu",
            "zhihu_type": "answer",
            "question_id": question_id,
            "answer_id": answer_id,
            "voteup_count": answer.get("voteupCount"),
            "comment_count": answer.get("commentCount"),
            "image_urls": _image_urls(answer),
        },
    }


def _nested_dict(data: dict[str, Any], *keys: str) -> dict[str, Any]:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return {}
        current = current.get(key)
    return current if isinstance(current, dict) else {}


def _join_content_array(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    chunks: list[str] = []
    for item in value:
        if isinstance(item, str):
            chunks.append(item)
        elif isinstance(item, dict):
            chunks.append(str(item.get("content") or item.get("text") or ""))
    return "\n".join(chunks)


class _TextExtractor(HTMLParser):
    _block_tags = {"p", "div", "br", "li", "section", "article", "h1", "h2", "h3", "h4", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._block_tags:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._block_tags:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data)

    def text(self) -> str:
        raw = html.unescape("".join(self.parts))
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in raw.splitlines()]
        return "\n".join(line for line in lines if line).strip()


def _html_to_text(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(value)
    return parser.text()


def _author_name(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    return value.get("name") or value.get("urlToken") or value.get("id")


def _author_id(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    raw = value.get("id") or value.get("urlToken")
    return str(raw) if raw else None


def _resolve_user(state: dict[str, Any], value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not value:
        return None
    users = _nested_dict(state, "entities", "users")
    user = users.get(str(value)) if isinstance(users, dict) else None
    return user if isinstance(user, dict) else None


def _first_non_empty(*values: str) -> str:
    for value in values:
        stripped = value.strip()
        if stripped:
            return stripped
    return "知乎内容"


def _short_title(value: str, limit: int = 80) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit].rstrip() if len(value) > limit else value


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _date_yyyymmdd(timestamp: int | None) -> str | None:
    if not timestamp:
        return None
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y%m%d")
    except (OSError, ValueError):
        return None


def _image_urls(value: Any) -> list[str]:
    urls: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                if key.lower() in {"url", "src", "thumbnail", "original"} and isinstance(child, str):
                    if child.startswith(("http://", "https://")) and _looks_like_image(child):
                        urls.append(child)
                else:
                    walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    seen = set()
    deduped = []
    for url in urls:
        if url not in seen:
            deduped.append(url)
            seen.add(url)
    return deduped


def _first_image(value: Any) -> str | None:
    urls = _image_urls(value)
    return urls[0] if urls else None


def _looks_like_image(url: str) -> bool:
    lowered = url.lower()
    return any(token in lowered for token in (".jpg", ".jpeg", ".png", ".webp", "pic", "image"))


def _guess_image_ext(url: str) -> str:
    match = re.search(r"\.([a-zA-Z0-9]{2,5})(?:[?#!]|$)", url.split("?")[0])
    if match:
        ext = match.group(1).lower()
        if ext in ("jpg", "jpeg", "png", "webp", "gif"):
            return ext
    return "jpg"


def _headers(referer: str) -> dict[str, str]:
    return {
        "User-Agent": _UA,
        "Referer": referer,
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
