"""Bilibili video download service.

Based on reference implementation with WBI signature support.
"""

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import get_settings
from app.models import MediaMetadata, MediaType

logger = logging.getLogger(__name__)

# Constants
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
REFERER = "https://www.bilibili.com/"

# WBI mixin table
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52
]

# BV/AV conversion constants
XOR_CODE = 23442827791579
MASK_CODE = 2251799813685247
MAX_AID = 1 << 51
BASE = 58
BV_TABLE = "FcwAPNKTMug3GV5Lj7EJnHpWsx4tb8haYeviqBz6rkCy12mUSDQX9RdoZf"


@dataclass
class QualityOption:
    """Quality option for video download."""
    qn: int
    fnval: int
    description: str = ""


@dataclass
class BiliVideoInfo:
    """Bilibili video info."""
    bvid: str
    title: str
    author: str
    play: int = 0
    duration: str = ""
    description: str = ""
    aid: int = 0
    cid: int = 0


@dataclass
class BiliCacheEntry:
    """Cache entry for downloaded videos."""
    bvid: str
    title: str
    file_path: str
    file_size: str
    quality: int
    format: str
    download_time: float
    author: str = "Unknown"
    play: int = 0


class BiliUtils:
    """Bilibili utility functions."""

    @staticmethod
    def get_mixin_key(orig: str) -> str:
        return "".join(orig[i] for i in MIXIN_KEY_ENC_TAB)[:32]

    @staticmethod
    def enc_wbi(params: dict[str, Any], img_key: str, sub_key: str) -> str:
        """Generate WBI signed query string."""
        mixin_key = BiliUtils.get_mixin_key(img_key + sub_key)
        curr_time = int(time.time())
        params["wts"] = curr_time

        chr_filter = re.compile(r"[!'()*]")
        sorted_params = sorted(params.items())
        query_items = []
        for key, value in sorted_params:
            value_str = chr_filter.sub("", str(value))
            query_items.append(f"{urlencode({key: ''})[:-1]}{urlencode({'': value_str})[1:]}")

        query = "&".join(query_items)
        wbi_sign = hashlib.md5((query + mixin_key).encode()).hexdigest()
        return f"{query}&w_rid={wbi_sign}"

    @staticmethod
    def av2bv(aid: int) -> str:
        """Convert AV number to BV ID."""
        bytes_list = list("BV1000000000")
        bv_index = len(bytes_list) - 1
        tmp = (MAX_AID | aid) ^ XOR_CODE

        while tmp > 0:
            bytes_list[bv_index] = BV_TABLE[tmp % BASE]
            tmp //= BASE
            bv_index -= 1

        bytes_list[3], bytes_list[9] = bytes_list[9], bytes_list[3]
        bytes_list[4], bytes_list[7] = bytes_list[7], bytes_list[4]

        return "".join(bytes_list)

    @staticmethod
    def bv2av(bvid: str) -> int:
        """Convert BV ID to AV number."""
        bvid_list = list(bvid)
        bvid_list[3], bvid_list[9] = bvid_list[9], bvid_list[3]
        bvid_list[4], bvid_list[7] = bvid_list[7], bvid_list[4]

        bvid_list = bvid_list[3:]
        tmp = 0
        for char in bvid_list:
            tmp = tmp * BASE + BV_TABLE.index(char)

        return (tmp & MASK_CODE) ^ XOR_CODE

    @staticmethod
    def clean_title(title: str) -> str:
        """Clean HTML tags and invalid filename characters."""
        title = re.sub(r"<[^>]*>", "", title)
        title = re.sub(r'[<>:"/\\|?*]', "_", title)
        return title.strip()

    @staticmethod
    def format_file_size(bytes_size: int) -> str:
        return f"{bytes_size / 1024 / 1024:.2f} MB"

    @staticmethod
    def parse_video_id(video_id: str) -> str:
        """Parse video ID from BV, AV, or URL."""
        video_id = video_id.strip()

        bv_match = re.search(r"(BV[a-zA-Z0-9]+)", video_id)
        if bv_match:
            return bv_match.group(1)

        av_match = re.search(r"av(\d+)", video_id, re.IGNORECASE)
        if av_match:
            return BiliUtils.av2bv(int(av_match.group(1)))

        if video_id.isdigit():
            return BiliUtils.av2bv(int(video_id))

        return video_id


class BiliAPI:
    """Bilibili API client."""

    QUALITY_OPTIONS = [
        QualityOption(64, 0, "720P FLV/MP4"),
        QualityOption(32, 0, "480P FLV/MP4"),
        QualityOption(16, 0, "360P FLV/MP4"),
        QualityOption(64, 16, "720P DASH"),
        QualityOption(32, 16, "480P DASH"),
        QualityOption(16, 16, "360P DASH"),
        QualityOption(64, 4048, "720P DASH Advanced"),
        QualityOption(32, 4048, "480P DASH Advanced"),
        QualityOption(16, 4048, "360P DASH Advanced"),
    ]

    def __init__(self):
        self._img_key: str = ""
        self._sub_key: str = ""
        self._buvid3: str = ""
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={"User-Agent": USER_AGENT, "Referer": REFERER},
                timeout=30.0,
                follow_redirects=True,
            )
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _ensure_wbi_keys(self):
        if not self._img_key or not self._sub_key:
            await self._fetch_wbi_keys()

    async def _fetch_wbi_keys(self):
        client = await self._get_client()
        resp = await client.get("https://api.bilibili.com/x/web-interface/nav")
        data = resp.json()

        wbi_img = data["data"]["wbi_img"]
        self._img_key = wbi_img["img_url"].rsplit("/", 1)[-1].rsplit(".", 1)[0]
        self._sub_key = wbi_img["sub_url"].rsplit("/", 1)[-1].rsplit(".", 1)[0]

    async def _ensure_buvid3(self):
        if not self._buvid3:
            await self._fetch_buvid3()

    async def _fetch_buvid3(self):
        client = await self._get_client()
        resp = await client.get("https://www.bilibili.com")

        for cookie in resp.cookies.jar:
            if cookie.name == "buvid3":
                self._buvid3 = f"buvid3={cookie.value}"
                return
        self._buvid3 = ""

    async def search(self, keyword: str, limit: int = 20) -> list[BiliVideoInfo]:
        """Search for videos."""
        await self._ensure_wbi_keys()
        await self._ensure_buvid3()

        params = {"keyword": keyword}
        query = BiliUtils.enc_wbi(params, self._img_key, self._sub_key)

        client = await self._get_client()
        url = f"https://api.bilibili.com/x/web-interface/wbi/search/all/v2?{query}"

        headers = {"Cookie": self._buvid3} if self._buvid3 else {}
        resp = await client.get(url, headers=headers)
        data = resp.json()

        if data["code"] != 0:
            raise Exception(f"Search failed: {data['code']} - {data.get('message', '')}")

        videos = []
        for result in data["data"]["result"]:
            if result["result_type"] == "video":
                for item in result["data"][:limit]:
                    videos.append(BiliVideoInfo(
                        bvid=item.get("bvid", ""),
                        title=BiliUtils.clean_title(item.get("title", "")),
                        author=item.get("author", ""),
                        play=item.get("play", 0),
                        duration=item.get("duration", ""),
                        description=item.get("description", ""),
                        aid=item.get("aid", 0),
                    ))
                break

        return videos

    async def get_page_list(self, bvid: str) -> list[dict]:
        """Get video page list."""
        await self._ensure_buvid3()

        client = await self._get_client()
        url = f"https://api.bilibili.com/x/player/pagelist?bvid={bvid}"

        headers = {"Cookie": self._buvid3} if self._buvid3 else {}
        resp = await client.get(url, headers=headers)
        data = resp.json()

        if data["code"] != 0:
            raise Exception(f"Get page list failed: {data['code']} - {data.get('message', '')}")

        return data["data"]

    async def get_play_url(self, bvid: str, cid: int, quality: QualityOption) -> dict:
        """Get video play URL."""
        await self._ensure_buvid3()

        params = {
            "bvid": bvid,
            "cid": cid,
            "qn": quality.qn,
            "fnval": quality.fnval,
            "fourk": 1,
            "platform": "html5",
        }

        client = await self._get_client()
        url = "https://api.bilibili.com/x/player/playurl"

        headers = {"Cookie": self._buvid3} if self._buvid3 else {}
        resp = await client.get(url, params=params, headers=headers)
        return resp.json()

    async def get_available_play_url(self, bvid: str, cid: int) -> tuple[dict, QualityOption]:
        """Get available play URL with auto quality selection."""
        for quality in self.QUALITY_OPTIONS:
            try:
                logger.debug(f"Trying quality: {quality.description}")
                play_url = await self.get_play_url(bvid, cid, quality)

                if play_url["code"] != 0:
                    continue

                data = play_url["data"]
                has_dash = data.get("dash") and data["dash"].get("video") and data["dash"].get("audio")
                has_durl = data.get("durl") and len(data["durl"]) > 0

                if has_dash or has_durl:
                    format_type = "DASH" if has_dash else "FLV/MP4"
                    logger.info(f"Got play URL: {quality.description} ({format_type})")
                    return play_url, quality

            except Exception as e:
                logger.warning(f"Failed to get play URL ({quality.description}): {e}")

        raise Exception("No available play URL found")

    async def get_subtitle_list(self, bvid: str, cid: int) -> list[dict]:
        """Get subtitle list."""
        await self._ensure_wbi_keys()
        await self._ensure_buvid3()

        params = {"bvid": bvid, "cid": str(cid)}
        query = BiliUtils.enc_wbi(params, self._img_key, self._sub_key)

        client = await self._get_client()
        url = f"https://api.bilibili.com/x/player/wbi/v2?{query}"

        headers = {"Cookie": self._buvid3} if self._buvid3 else {}
        resp = await client.get(url, headers=headers)
        data = resp.json()

        if data["code"] != 0:
            return []

        subtitle_info = data.get("data", {}).get("subtitle", {})
        return subtitle_info.get("subtitles", [])

    async def download_subtitle_json(self, subtitle_url: str) -> dict | None:
        """Download subtitle JSON."""
        try:
            client = await self._get_client()
            if subtitle_url.startswith("//"):
                subtitle_url = "https:" + subtitle_url

            resp = await client.get(subtitle_url)
            return resp.json()
        except Exception as e:
            logger.warning(f"Failed to download subtitle: {e}")
            return None


class SubtitleConverter:
    """Subtitle format converter."""

    @staticmethod
    def parse_bili_json(json_data: dict) -> list[tuple[float, float, str]]:
        """Parse Bilibili JSON subtitle format."""
        items = []
        for item in json_data.get("body", []):
            items.append((
                item.get("from", 0.0),
                item.get("to", 0.0),
                item.get("content", ""),
            ))
        return items

    @staticmethod
    def _format_srt_time(seconds: float) -> str:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    @classmethod
    def to_srt(cls, items: list[tuple[float, float, str]]) -> str:
        """Convert to SRT format."""
        lines = []
        for i, (start, end, content) in enumerate(items, 1):
            start_time = cls._format_srt_time(start)
            end_time = cls._format_srt_time(end)
            lines.append(f"{i}")
            lines.append(f"{start_time} --> {end_time}")
            lines.append(content)
            lines.append("")
        return "\n".join(lines)


class BiliCacheManager:
    """Video cache manager."""

    def __init__(self, download_dir: Path, cache_file: str = "bili_cache.json"):
        self.download_dir = download_dir
        self.cache_file = download_dir / cache_file
        self._cache: dict[str, BiliCacheEntry] = {}
        self._load_cache()

    def _load_cache(self):
        try:
            if self.cache_file.exists():
                content = self.cache_file.read_text(encoding="utf-8").strip()
                if content:
                    data = json.loads(content)
                    self._cache = {k: BiliCacheEntry(**v) for k, v in data.items()}
                    logger.info(f"Loaded Bilibili cache: {len(self._cache)} entries")
        except Exception as e:
            logger.warning(f"Failed to load Bilibili cache: {e}")
            self._cache = {}

    def _save_cache(self):
        try:
            self.download_dir.mkdir(parents=True, exist_ok=True)
            data = {k: asdict(v) for k, v in self._cache.items()}
            self.cache_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to save Bilibili cache: {e}")

    def has_cached(self, bvid: str) -> bool:
        entry = self._cache.get(bvid)
        if not entry:
            return False

        if not Path(entry.file_path).exists():
            del self._cache[bvid]
            self._save_cache()
            return False

        return True

    def get_cached(self, bvid: str) -> BiliCacheEntry | None:
        return self._cache.get(bvid)

    def add(self, entry: BiliCacheEntry):
        self._cache[entry.bvid] = entry
        self._save_cache()
        logger.info(f"Added to Bilibili cache: {entry.bvid} - {entry.title}")


class BilibiliService:
    """Bilibili video download service."""

    def __init__(self):
        self._settings = get_settings()
        self._api = BiliAPI()
        self._cache_manager: BiliCacheManager | None = None
        self._initialized = False

    def _ensure_init(self):
        if self._initialized:
            return
        output_dir = self._settings.data_processing.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        self._cache_manager = BiliCacheManager(output_dir)
        self._initialized = True

    async def close(self):
        await self._api.close()

    async def search(self, keyword: str, limit: int = 10) -> list[dict[str, Any]]:
        """Search for videos."""
        videos = await self._api.search(keyword, limit)
        return [
            {
                "bvid": v.bvid,
                "title": v.title,
                "author": v.author,
                "play": v.play,
                "duration": v.duration,
                "description": v.description,
            }
            for v in videos
        ]

    async def download(
        self,
        video_id: str,
        use_cache: bool = True,
        extract_audio: bool = True,
    ) -> dict[str, Any]:
        """Download a Bilibili video.

        Args:
            video_id: BV ID, AV number, or URL
            use_cache: Whether to use cached video
            extract_audio: Whether to extract audio (WAV) for transcription

        Returns:
            Dict with file_path, metadata, and optionally audio_path
        """
        self._ensure_init()
        bvid = BiliUtils.parse_video_id(video_id)

        # Check cache
        if use_cache and self._cache_manager and self._cache_manager.has_cached(bvid):
            cached = self._cache_manager.get_cached(bvid)
            if cached and Path(cached.file_path).exists():
                logger.info(f"Using cached video: {bvid}")
                return {
                    "file_path": cached.file_path,
                    "metadata": {
                        "title": cached.title,
                        "source_url": f"https://www.bilibili.com/video/{bvid}",
                        "uploader": cached.author,
                        "media_type": "video",
                    },
                }

        # Get video info
        logger.info(f"Downloading Bilibili video: {bvid}")
        page_list = await self._api.get_page_list(bvid)

        if not page_list:
            raise RuntimeError(f"Failed to get video info: {bvid}")

        first_page = page_list[0]
        cid = first_page["cid"]
        title = BiliUtils.clean_title(first_page.get("part", bvid))

        # Get play URL
        play_url_data, quality = await self._api.get_available_play_url(bvid, cid)
        data = play_url_data["data"]

        output_dir = self._settings.data_processing.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        output_path: Path

        # Download based on format
        if data.get("dash") and data["dash"].get("video") and data["dash"].get("audio"):
            # DASH format - download and merge
            video_info = data["dash"]["video"][0]
            audio_info = data["dash"]["audio"][0]

            video_path = output_dir / f"{bvid}_video.m4s"
            audio_path = output_dir / f"{bvid}_audio.m4s"
            output_path = output_dir / f"{title}_{bvid}.mp4"

            logger.info("Downloading DASH video stream...")
            await self._download_file(video_info["baseUrl"], video_path)

            logger.info("Downloading DASH audio stream...")
            await self._download_file(audio_info["baseUrl"], audio_path)

            logger.info("Merging video and audio...")
            await self._merge_video_audio(video_path, audio_path, output_path)

            # Cleanup temp files
            video_path.unlink(missing_ok=True)
            audio_path.unlink(missing_ok=True)
            format_type = "DASH"

        elif data.get("durl") and len(data["durl"]) > 0:
            # FLV/MP4 format
            video_info = data["durl"][0]
            output_path = output_dir / f"{title}_{bvid}.mp4"

            logger.info("Downloading FLV/MP4 video...")
            await self._download_file(video_info["url"], output_path)
            format_type = "FLV/MP4"
        else:
            raise RuntimeError("No available video stream found")

        # Download subtitles
        await self._download_subtitles(bvid, cid, output_dir, f"{title}_{bvid}")

        # Get file size
        file_size = BiliUtils.format_file_size(output_path.stat().st_size)

        # Add to cache
        if self._cache_manager:
            cache_entry = BiliCacheEntry(
                bvid=bvid,
                title=title,
                file_path=str(output_path),
                file_size=file_size,
                quality=quality.qn,
                format=format_type,
                download_time=time.time(),
            )
            self._cache_manager.add(cache_entry)

        result = {
            "file_path": str(output_path),
            "metadata": {
                "title": title,
                "source_url": f"https://www.bilibili.com/video/{bvid}",
                "uploader": None,  # Would need another API call
                "media_type": "video",
                "bvid": bvid,
                "quality": quality.qn,
                "format": format_type,
                "file_size": file_size,
            },
        }

        # Extract audio if requested
        if extract_audio:
            audio_output = output_dir / f"{title}_{bvid}.wav"
            await self._extract_audio(output_path, audio_output)
            result["audio_path"] = str(audio_output)

        return result

    async def _download_file(self, url: str, dest: Path):
        """Download file with progress."""
        logger.info(f"Downloading to: {dest}")

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=30.0, read=300.0, write=30.0, pool=30.0),
            follow_redirects=True,
        ) as client:
            headers = {"User-Agent": USER_AGENT, "Referer": REFERER}

            async with client.stream("GET", url, headers=headers) as resp:
                resp.raise_for_status()
                total_size = int(resp.headers.get("content-length", 0))
                downloaded = 0

                with open(dest, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        f.write(chunk)
                        downloaded += len(chunk)

                        if total_size > 0:
                            percent = (downloaded / total_size) * 100
                            if downloaded % (1024 * 1024) < 65536:  # Log every ~1MB
                                logger.debug(f"Progress: {downloaded / 1024 / 1024:.1f}/{total_size / 1024 / 1024:.1f} MB ({percent:.1f}%)")

    async def _merge_video_audio(self, video_path: Path, audio_path: Path, output_path: Path):
        """Merge video and audio using FFmpeg."""
        import asyncio

        cmd = [
            "ffmpeg",
            "-i", str(video_path),
            "-i", str(audio_path),
            "-c", "copy",
            "-y",
            str(output_path),
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        _, stderr = await process.communicate()

        if process.returncode != 0:
            raise RuntimeError(f"FFmpeg merge failed: {stderr.decode()}")

    async def _extract_audio(self, video_path: Path, audio_path: Path):
        """Extract audio from video using FFmpeg."""
        import asyncio

        cmd = [
            "ffmpeg",
            "-i", str(video_path),
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            "-y",
            str(audio_path),
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        _, stderr = await process.communicate()

        if process.returncode != 0:
            raise RuntimeError(f"FFmpeg audio extraction failed: {stderr.decode()}")

        logger.info(f"Extracted audio: {audio_path}")

    async def _download_subtitles(self, bvid: str, cid: int, output_dir: Path, base_name: str) -> list[str]:
        """Download available subtitles."""
        downloaded_files = []

        try:
            subtitles = await self._api.get_subtitle_list(bvid, cid)

            if not subtitles:
                logger.info("No subtitles available for this video")
                return downloaded_files

            logger.info(f"Found {len(subtitles)} subtitle(s)")

            for sub in subtitles:
                lan = sub.get("lan", "unknown")
                lan_doc = sub.get("lan_doc", lan)
                subtitle_url = sub.get("subtitle_url", "")

                if not subtitle_url:
                    continue

                logger.info(f"Downloading subtitle: {lan_doc} ({lan})")

                json_data = await self._api.download_subtitle_json(subtitle_url)
                if not json_data:
                    continue

                items = SubtitleConverter.parse_bili_json(json_data)
                if not items:
                    continue

                # Save SRT format
                srt_content = SubtitleConverter.to_srt(items)
                srt_path = output_dir / f"{base_name}.{lan}.srt"
                srt_path.write_text(srt_content, encoding="utf-8")
                downloaded_files.append(str(srt_path))
                logger.info(f"Saved subtitle: {srt_path.name}")

        except Exception as e:
            logger.warning(f"Subtitle download error: {e}")

        return downloaded_files

    def extract_metadata(self, result: dict[str, Any]) -> MediaMetadata:
        """Extract MediaMetadata from download result."""
        metadata = result.get("metadata", {})

        return MediaMetadata(
            title=metadata.get("title", "Unknown"),
            source_url=metadata.get("source_url"),
            uploader=metadata.get("uploader"),
            upload_date=None,
            duration_seconds=None,
            media_type=MediaType.VIDEO,
            file_path=result.get("file_path"),
            file_hash=None,
        )


# Singleton instance
_service: BilibiliService | None = None


def get_bilibili_service() -> BilibiliService:
    global _service
    if _service is None:
        _service = BilibiliService()
    return _service


async def download_bilibili(video_id: str, extract_audio: bool = True) -> dict[str, Any]:
    """Download a Bilibili video.

    Args:
        video_id: BV ID, AV number, or URL
        extract_audio: Whether to extract audio for transcription

    Returns:
        Dict with file_path, audio_path (if extract_audio), and metadata
    """
    service = get_bilibili_service()
    return await service.download(video_id, extract_audio=extract_audio)


async def search_bilibili(keyword: str, limit: int = 10) -> list[dict[str, Any]]:
    """Search for Bilibili videos.

    Args:
        keyword: Search keyword
        limit: Maximum number of results

    Returns:
        List of video info dicts
    """
    service = get_bilibili_service()
    return await service.search(keyword, limit)
