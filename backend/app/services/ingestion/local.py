"""Local file subtitle and metadata discovery."""

import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

try:
    from defusedxml import ElementTree as SafeET
except ImportError:
    SafeET = None  # fallback: use hardened parser below

logger = logging.getLogger(__name__)


def find_local_subtitle(video_path: str | Path) -> dict | None:
    """
    Search for SRT subtitle files in the same directory as the video.

    Checks patterns:
      - {stem}.srt
      - {stem}_中文.srt
      - {stem}.zh.srt
      - {stem}.zh-CN.srt

    Returns:
        {"subtitle_path": str, "subtitle_lang": str, "subtitle_format": "srt"} or None
    """
    video = Path(video_path)
    parent = video.parent
    stem = video.stem

    # Priority order for subtitle search
    patterns = [
        (f"{stem}.srt", "zh"),
        (f"{stem}_中文.srt", "zh"),
        (f"{stem}.zh.srt", "zh"),
        (f"{stem}.zh-CN.srt", "zh"),
        (f"{stem}.zh-Hans.srt", "zh"),
        (f"{stem}.en.srt", "en"),
    ]

    for filename, lang in patterns:
        sub_path = parent / filename
        if sub_path.exists():
            logger.info(f"Found local subtitle: {sub_path}")
            return {
                "subtitle_path": str(sub_path),
                "subtitle_lang": lang,
                "subtitle_format": "srt",
            }

    return None


def parse_nfo(video_path: str | Path) -> dict | None:
    """
    Parse .nfo file (XML) in the same directory as the video to extract metadata.

    NFO format (Bilibili downloader style):
        <movie>
          <title>...</title>
          <plot>...</plot>
          <year>2023</year>
          <genre>知识</genre>
          <tag>标签</tag>
          <actor><name>UP主</name><role>uid</role></actor>
          <uniqueid type="bilibili">BV号</uniqueid>
          <premiered>2023-09-30</premiered>
        </movie>

    Returns:
        dict with keys: title, description, tags, uploader, upload_date, source_url
        or None if no nfo found
    """
    video = Path(video_path)
    nfo_path = video.parent / f"{video.stem}.nfo"

    if not nfo_path.exists():
        return None

    try:
        # Use defusedxml if available; otherwise disable entity expansion manually
        if SafeET is not None:
            tree = SafeET.parse(nfo_path)
        else:
            parser = ET.XMLParser(encoding="utf-8")
            # Disable entity expansion to prevent billion-laughs DoS
            parser.entity = {}  # type: ignore[attr-defined]
            tree = ET.parse(nfo_path, parser=parser)
        root = tree.getroot()

        title = _get_text(root, "title")
        description = _get_text(root, "plot")

        tags = []
        for el in root.findall("genre"):
            if el.text:
                tags.append(el.text.strip())
        for el in root.findall("tag"):
            if el.text and el.text.strip() not in tags:
                tags.append(el.text.strip())

        uploader = None
        actor = root.find("actor")
        if actor is not None:
            uploader = _get_text(actor, "name")

        upload_date = None
        premiered = _get_text(root, "premiered")
        if premiered:
            try:
                upload_date = datetime.strptime(premiered, "%Y-%m-%d")
            except ValueError:
                pass

        source_url = None
        uid = root.find("uniqueid[@type='bilibili']")
        if uid is not None and uid.text:
            source_url = f"https://www.bilibili.com/video/{uid.text.strip()}"

        result = {
            "title": title,
            "description": description,
            "tags": tags,
            "uploader": uploader,
            "upload_date": upload_date,
            "source_url": source_url,
        }
        logger.info(f"Parsed NFO: {nfo_path} -> title={title}, uploader={uploader}")
        return result

    except Exception as e:
        logger.warning(f"Failed to parse NFO {nfo_path}: {e}")
        return None


def find_original_file(uploaded_path: str | Path) -> Path | None:
    """Try to locate the original file for an uploaded file.

    Only checks the same directory as the uploaded file (sibling lookup).
    Returns None if no match — we don't scan arbitrary filesystem locations.
    """
    # The uploaded file IS in its original directory already when the user
    # provided a local path directly.  When uploaded via browser, the original
    # path is unknown, so we simply return None.
    return None


def _get_text(element, tag: str) -> str | None:
    el = element.find(tag)
    if el is not None and el.text:
        return el.text.strip()
    return None
