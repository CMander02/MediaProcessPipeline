from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.api.routes import search as search_routes  # noqa: E402
from app.cli.client import MppClient, MppClientError  # noqa: E402
from app.cli.commands import archive, read, search, task_views  # noqa: E402
from app.cli.main import app  # noqa: E402
from app.core import archive_sync, database, settings  # noqa: E402
from app.core.artifacts import get_artifact_store  # noqa: E402
from app.core.library import get_library_service  # noqa: E402
from app.models import Task, TaskStatus, TaskType  # noqa: E402
from app.services import transcript_search  # noqa: E402

runner = CliRunner()
TITLE = 'A "Quoted" Interview'
YOUTUBE = "https://www.youtube.com/watch?v=fixture"
BILIBILI = "https://www.bilibili.com/video/BVfixture?p=2"


def _srt(*cues: tuple[int, int, str]) -> str:
    return "\n".join(
        f"{i}\n00:00:{start:02d},000 --> 00:00:{end:02d},000\n{text}\n"
        for i, (start, end, text) in enumerate(cues, 1)
    )


POLISHED = _srt(
    (1, 3, "[Alice] First line"),
    (11, 14, "[Bob] Graph search"),
    (21, 23, "[unknown] Pending line"),
    (31, 34, "[Alice] Final line"),
)
RAW = _srt((1, 3, "[Alice] Raw reproduction"))


def _files() -> dict[str, str]:
    metadata = {
        "title": TITLE,
        "platform": "youtube",
        "uploader": "Producer",
        "duration": 40,
        "source_url": YOUTUBE,
        "media_type": "video",
        "media_removed": True,
        "media_retention_applied": True,
        "extra": {
            "derived_content_stale": True,
            "speaker_quality": {"unassigned_segments": 1},
        },
    }
    timeline = [
        {
            "start": 0, "end": 15, "title": "Opening", "summary": "First section",
            "children": [{"start": 10, "end": 15, "title": "Graph discussion"}],
        },
        {"start": 20, "title": "Conclusion"},
    ]
    tree = {"title": "Interview", "children": [
        {"title": "Opening", "start": 0, "children": [{"title": "Nested detail", "start": 1}]},
        {"title": "Conclusion", "start": 20},
    ]}
    return {
        "metadata.json": json.dumps(metadata),
        "summary.json": json.dumps({"timeline": timeline, "tldr": "One-line recap",
                                    "topics": ["graphs", "systems"]}),
        "summary.md": "# Summary\n\nOne-line recap",
        "detail.md": "Detailed text",
        "analysis.json": json.dumps({"topics": ["graphs"], "audience": {"level": "intro"}}),
        "source.md": "# Source\n\nBefore ![picture](images/01.jpg) after.\n",
        "mindmap.json": json.dumps(tree),
        "mindmap.md": "## Markdown tree",
        "transcript_polished.md": "Markdown transcript",
        "transcript_polished.srt": POLISHED,
        "transcript.srt": RAW,
        "transcript.zh-CN.srt": _srt((1, 3, "第一段"), (11, 14, "第二段")),
        "transcript.en.srt": _srt((1, 2, "First translation"), (11, 13, "Second translation")),
        "descriptions/01.md": "Visible diagram and OCR",
        "descriptions/02.md": "Unreferenced image",
        "participant_evidence.json": "{}",
        "speaker_map.json": '{"SPEAKER_00":"Alice"}',
    }


class Daemon:
    def __init__(self, *, running=True, remote=False):
        self.running = running
        self.remote = remote
        self.files = _files()
        self.root = r"D:\Library\Archive" if remote else "/library/archive"
        self.task_id = str(uuid4())
        self.archive_id = str(uuid4())
        self.archive = {
            "archive_id": self.archive_id, "task_id": self.task_id, "path": self.root,
            "title": TITLE, "folder_id": "folder-1", "attempts": 2,
            "metadata": json.loads(self.files["metadata.json"]),
        }
        self.calls = []

    def ping(self):
        return self.running

    def list_archives(self, *, lite=False):
        return [self.archive]

    def get_archive(self, path):
        assert path == self.root
        return self.archive

    def list_tasks(self, *, limit, offset=0, **kwargs):
        return [self.get_task(self.task_id)][offset:offset + limit]

    def get_task(self, task_id):
        assert task_id == self.task_id
        return {
            "id": task_id, "status": "completed", "source": YOUTUBE,
            "result": {"output_dir": self.root, "image_descriptions": [
                {"index": 3, "text": "Description from task result"},
            ]},
        }

    def _relative(self, path):
        assert path.replace("\\", "/").startswith(self.root.replace("\\", "/") + "/")
        return path.replace("\\", "/")[len(self.root) + 1:]

    def fs_read(self, path, *, task_id=None):
        name = self._relative(path)
        self.calls.append(("read", name, task_id))
        assert task_id == self.task_id
        if self.remote:
            if name.startswith("descriptions/"):
                return {"success": False}
            code = 404 if name == "transcript_polished.srt" else 403
            raise MppClientError("blocked", "fs_read failed", status_code=code)
        return {"success": name in self.files, "content": self.files.get(name)}

    def capabilities(self):
        return {"filesystem_browse": not self.remote}

    def fs_list(self, path, mode):
        assert not self.remote, "Remote browse must not be called"
        self.calls.append(("browse", path))
        prefix = "" if path == self.root else self._relative(path) + "/"
        return {"items": [
            {"name": name.removeprefix(prefix)}
            for name in self.files
            if name.startswith(prefix) and "/" not in name.removeprefix(prefix)
        ]}

    def sync_file(self, archive_id, name):
        self.calls.append(("sync_file", name))
        assert archive_id == self.archive_id
        if name not in self.files:
            raise MppClientError("not_found", name, status_code=404)
        return self.files[name].encode("utf-8-sig")

    def sync_manifest(self, archive_id):
        self.calls.append(("manifest", archive_id))
        assert archive_id == self.archive_id
        return {"files": [{"relative_path": name} for name in self.files]}

    def fs_write(self, path, content, *, expected_content):
        name = self._relative(path)
        self.calls.append(("write", name, expected_content))
        if self.files[name] != expected_content:
            raise MppClientError("state_conflict", "File was changed", status_code=409, exit_code=4)
        self.files[name] = content
        return {"success": True, "path": path}

    def list_archives_paged(self, **params):
        self.calls.append(("page", params))
        return {
            "archives": [self.archive], "total": 6, "page": params["page"],
            "facets": {"media": {"video": 1, "text": 5}},
        }

    def list_library_folders(self):
        return {"folders": [{"id": "folder-1", "name": "Interviews"}]}

    def search_transcripts(self, query, *, limit, speaker=None):
        self.calls.append(("search", query, limit))
        return {"query": query, "groups": [{
            "title": TITLE, "path": self.root, "count": 2, "hits": [
                {"start_ms": 11000, "speaker": "Bob", "text": "Graph search"},
                {"start_ms": 31000, "speaker": "Alice", "text": "Final search"},
            ],
        }]}

    def media_retention(self, path, policy, files=None):
        self.calls.append(("retention", path, policy, files))
        if files is not None:
            return {"cleaned": [{"path": files[0]}], "reclaimed_bytes": 44, "errors": []}
        return {"path": path, "policy": policy, "entries": [
            {"path": "original.mp4", "delete": True, "bytes": 44},
            {"path": "source.md", "delete": False, "bytes": 12},
        ]}

    def export_task_archive(self, task_id, path, *, include_media):
        self.calls.append(("zip", task_id, include_media))
        path.write_bytes(b"portable zip bytes")
        return {"size": path.stat().st_size}

    def duplicate_cleanup_preview(self):
        self.calls.append(("dedupe_preview",))
        return {"count": 1, "items": [{"path": self.root, "title": TITLE}]}

    def duplicate_cleanup_apply(self):
        self.calls.append(("dedupe_apply",))
        return {"deleted": 1, "errors": []}

    def archive_index_status(self):
        self.calls.append(("index",))
        return {"revision": 1}

    def reconcile_archives(self):
        self.calls.append(("reconcile",))
        return {"revision": 2}

    def artifacts_status(self, task_id):
        self.calls.append(("artifacts", task_id))
        return {"task_id": task_id, "artifacts": [
            {"filename": "detail.md", "state": "mirror_missing"},
            {"filename": "summary.md", "state": "file_missing"},
        ]}

    def artifacts_repair(self, params):
        self.calls.append(("repair", params))
        return {"success": True, "path": params.get("filename")}


def _connect(monkeypatch, daemon):
    for module in (archive, read, search):
        monkeypatch.setattr(module, "client", lambda: daemon)
    monkeypatch.setattr(task_views, "_get_client", lambda: daemon)


def _invoke(*args, remote=False, json_mode=False, expected=0):
    route = ["--server", "https://remote.example"] if remote else ["--local"]
    prefix = ["--skip-version-check", *route, *(["--json"] if json_mode else [])]
    result = runner.invoke(
        app, [*prefix, *args], env={"MPP_PLAIN_OUTPUT": "0", "MPP_NO_COLOR": "0"},
    )
    assert result.exit_code == expected, (result.output, result.exception)
    return result.stdout


@pytest.fixture
def daemon(monkeypatch):
    api = Daemon()
    _connect(monkeypatch, api)
    return api


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "_runtime_settings", settings.RuntimeSettings(
        data_root=str(tmp_path),
    ))
    monkeypatch.setattr(archive_sync, "_sync_service", archive_sync.ArchiveSyncService())
    monkeypatch.setattr(transcript_search, "_service", transcript_search.TranscriptSearchService())
    _connect(monkeypatch, Daemon(running=False))
    database.reset_db_path(tmp_path)
    directory = tmp_path / "archives" / "Interview"
    directory.mkdir(parents=True)
    task = Task(
        task_type=TaskType.PIPELINE, status=TaskStatus.COMPLETED, source=YOUTUBE,
        result={"output_dir": str(directory)},
    )
    database.get_task_store().save(task)
    for filename, content in _files().items():
        get_artifact_store().write(task.id, directory, filename, content)
    yield task, directory
    database.close_db()


def test_read_overview_tree_chapters_and_artifact_views(daemon):
    overview = _invoke("read", TITLE)
    assert all(value in overview for value in (
        TITLE, "youtube", "Producer", "folder-1", "duration\t40", YOUTUBE,
        "speaker_count\t2", "pending_unknown_cues\t1", "[摘要待更新]", "One-line recap",
    ))
    assert overview.index("[摘要待更新]") < overview.index("Opening") < overview.index("# Summary")
    tree = _invoke("read", TITLE, "-v", "mindmap", "--plain", "--depth", "1", "--no-timestamps")
    assert "|-- Opening" in tree and "`-- Conclusion" in tree
    assert "Nested detail" not in tree and "00:00:00" not in tree
    assert "├── Opening\t00:00:00.000" in _invoke("read", TITLE, "-v", "mindmap")
    assert "## Markdown tree" in _invoke("read", TITLE, "-v", "mindmap", "--raw-md")
    del daemon.files["mindmap.json"]
    assert "## Markdown tree" in _invoke("read", TITLE, "-v", "mindmap")
    rows = _invoke("read", TITLE, "-v", "chapters")
    assert "INDEX\tSTART\tEND\tTITLE\tSUMMARY" in rows
    assert "2\t00:00:10.000\t00:00:15.000\t  Graph discussion" in rows
    assert "3\t00:00:20.000\t00:00:40.000\tConclusion" in rows
    assert "Detailed text" in _invoke("read", TITLE, "-v", "detail")
    assert "## topics" in _invoke("read", TITLE, "-v", "analysis")
    assert "![picture]" in _invoke("read", TITLE, "-v", "source")
    del daemon.files["summary.md"]
    assert "# Source" in _invoke("read", TITLE)
    assert all(call[0] != "browse" for call in daemon.calls)


@pytest.mark.parametrize(("options", "wanted", "absent"), [
    (["--at", "00:12", "--window", "4"], "Graph search", "First line"),
    (["--range", "00:19-00:25"], "Pending line", "Final line"),
    (["--chapter", "2"], "Graph search", "Pending line"),
    (["--cue", "2"], "Graph search", "First line"),
    (["--cue", "2-3"], "Pending line", "Final line"),
    (["--speaker", "aLiCe"], "Final line", "Graph search"),
    (["--grep", "graph", "-C", "1"], "Pending line", "Final line"),
    (["--view", "raw"], "Raw reproduction", "First line"),
    (["--lang", "zh-cn"], "第一段", "First line"),
    (["--bilingual", "zh-CN,en", "--no-timestamps"], "第一段\n    First translation", "00:00"),
])
def test_read_transcript_slices(daemon, options, wanted, absent):
    output = _invoke("read", daemon.archive_id, *options)
    assert wanted in output and absent not in output


def test_read_remote_sync_fallback_and_notes(monkeypatch):
    api = Daemon(remote=True)
    _connect(monkeypatch, api)
    notes = _invoke("read", TITLE, "-v", "notes", remote=True)
    assert "Before" in notes and "after." in notes
    assert "![picture]" not in notes
    assert "Before" in notes.split("[配图 01 | 视觉与 OCR 描述]")[0]
    assert notes.index("Visible diagram and OCR") < notes.index("after.")
    assert notes.index("after.") < notes.index("Unreferenced image")
    assert "Description from task result" in notes
    assert "第一段" in _invoke("read", TITLE, "--lang", "zh-CN", remote=True)
    assert "[00:00:11] [Bob] Graph search" in _invoke(
        "read", TITLE, "-v", "transcript", remote=True,
    )
    assert ("manifest", api.archive_id) in api.calls
    assert ("sync_file", "descriptions/01.md") in api.calls
    assert ("sync_file", "source.md") in api.calls
    assert ("sync_file", "transcript_polished.srt") in api.calls
    assert not any(call[0] == "browse" for call in api.calls)


@pytest.mark.parametrize(("url", "platform", "expected"), [
    (YOUTUBE, "youtube", YOUTUBE + "&t=65s"),
    (BILIBILI, "bilibili", BILIBILI + "&t=65"),
    ("https://youtu.be/fixture", "youtube", "https://youtu.be/fixture?t=65s"),
    ("https://example.com/talk#intro", "webpage", "https://example.com/talk#t=65"),
])
def test_url_at(daemon, url, platform, expected):
    daemon.files["metadata.json"] = json.dumps({"source_url": url, "platform": platform})
    assert _invoke("read", TITLE, "--url-at", "01:05").strip() == expected


def test_read_unindexed_online_task(daemon, monkeypatch):
    monkeypatch.setattr(daemon, "list_archives", lambda **kw: [])
    assert "Detailed text" in _invoke("read", daemon.task_id[:8], "-v", "detail")


def test_read_offline_disk_and_mirror(library):
    task, directory = library
    assert "Detailed text" in _invoke("read", str(directory), "-v", "detail")
    assert "One-line recap" in _invoke("read", "@last", "-v", "summary")
    metadata = json.loads((directory / "metadata.json").read_text())
    (directory / "summary.md").unlink()
    assert "One-line recap" in _invoke("read", task.id.hex[:8], "-v", "summary")
    assert "One-line recap" in _invoke("read", metadata["archive_id"], "-v", "summary")
    (directory / "descriptions/01.md").unlink()
    assert "Visible diagram and OCR" in _invoke("read", TITLE, "-v", "notes")
    payload = _invoke("read", TITLE, remote=True, json_mode=True, expected=3)
    assert json.loads(payload)["error"]["code"] == "daemon_unavailable"


def test_search_online_and_speaker_filter(daemon):
    output = _invoke("search", "search", "-n", "3")
    assert "Title matches" in output and "(2 hits)" in output
    assert "[00:00:11] [Bob] Graph search" in output
    assert f'mpp --local read "{daemon.archive_id}" --at 00:00:11' in output
    assert ("search", "search", 3) in daemon.calls
    assert ("page", {"page": 1, "page_size": 3, "search": "search"}) in daemon.calls
    output = _invoke("search", "search", "--speaker", "BOB")
    assert "Graph search" in output and "Final search" not in output


def test_search_offline_cold_start_and_refresh(library):
    task, directory = library
    service = transcript_search.get_transcript_search_service()
    assert not service._db_path().exists()
    output = _invoke("search", "graph search", "--speaker", "bOb")
    assert "[00:00:11] [Bob] Graph search" in output
    hint = next(line.strip() for line in output.splitlines() if line.strip().startswith("mpp "))
    command = shlex.split(hint)
    assert command == ["mpp", "--local", "read", str(directory), "--at", "00:00:11"]
    assert "Graph search" in _invoke(*command[2:])
    assert service._db_path().exists()
    assert "No matches." in _invoke("search", "graph search", "--speaker", "Alice")
    get_artifact_store().write(
        task.id, directory, "transcript_polished.srt",
        _srt((1, 3, "[Alice] Refreshed transcript")),
    )
    assert "Refreshed transcript" in _invoke("search", "refreshed", "--refresh")


def test_online_search_filters_before_cue_sampling(library, monkeypatch):
    task, directory = library
    get_artifact_store().write(
        task.id, directory, "transcript_polished.srt",
        _srt((1, 3, "[Alice] Index hit one"), (4, 6, "[Alice] Index hit two"),
             (7, 9, "[Alice] Index hit three"), (10, 12, "[Bob] Index hit four")),
    )
    transcript_search.get_transcript_search_service().refresh()
    server = FastAPI()
    server.include_router(search_routes.router, prefix="/api")

    api = MppClient("http://localhost:18000")
    api.close()
    with TestClient(server) as test_server:
        def handler(request):
            return test_server.get(request.url.path, params=request.url.params)

        with httpx.Client(base_url=api.base_url, transport=httpx.MockTransport(handler)) as http:
            api._client = http
            monkeypatch.setattr(api, "ping", lambda: True)
            monkeypatch.setattr(api, "list_archives_paged", lambda **kw: {"archives": []})
            _connect(monkeypatch, api)
            output = _invoke("search", "index hit", "--speaker", "bOB")

    assert "[00:00:10] [Bob] Index hit four" in output
    assert "(1 hit)" in output and "Alice" not in output


@pytest.mark.parametrize("remote", [False, True])
def test_show_artifacts_online(remote, monkeypatch):
    api = Daemon(remote=remote)
    _connect(monkeypatch, api)
    args = ("show", api.task_id[:8], "--summary", "--transcript")
    output = _invoke(*args, remote=remote)
    assert "One-line recap" in output and output.count("Graph search") == 1
    assert "Raw reproduction" not in output and "Markdown transcript" not in output
    assert all(call[0] != "browse" for call in api.calls)
    payload = json.loads(_invoke(*args, "--detail", "--mindmap", "--json", remote=remote))["data"]
    assert payload["id"] == api.task_id
    assert payload["artifacts"]["transcript"] == POLISHED
    assert payload["artifacts"]["detail"] == "Detailed text"
    assert "├── Opening" in payload["artifacts"]["mindmap"]


@pytest.mark.parametrize("remote", [False, True])
def test_show_reads_legacy_artifact_names(remote, monkeypatch):
    api = Daemon(remote=remote)
    api.files = {
        "interview_summary.md": "Legacy summary",
        "transcript.txt": "Legacy transcript",
    }
    _connect(monkeypatch, api)
    output = _invoke("show", api.task_id[:8], "--summary", "--transcript", remote=remote)
    assert output == "Legacy summary\nLegacy transcript\n"


def test_show_offline_prefers_polished_and_emits_structured_missing(library):
    task, directory = library
    prefix = str(task.id)[:8]
    (directory / "transcript_polished.srt").unlink()
    output = _invoke("show", prefix, "--summary", "--transcript")
    assert output.count("Graph search") == 1 and "Raw reproduction" not in output
    payload = json.loads(_invoke("show", prefix, "--json"))["data"]
    assert payload["id"] == str(task.id) and "artifacts" not in payload
    tree = json.loads(_invoke("show", prefix, "--mindmap", "--json"))["data"]
    assert "Interview" in tree["artifacts"]["mindmap"]
    pending = Task(task_type=TaskType.PIPELINE, status=TaskStatus.PROCESSING, source=YOUTUBE,
                   result={"output_dir": str(directory / "pending")})
    database.get_task_store().save(pending)
    missing = _invoke("show", str(pending.id)[:8], "--detail", "--json", expected=4)
    assert json.loads(missing)["error"]["code"] == "task_artifact_missing"
    error = json.loads(_invoke("show", "abcd0000", "--summary", "--json", expected=4))
    assert error["error"]["code"] == "task_not_found"


def test_archive_list_filters_facets_and_badges(daemon):
    args = (
        "archive", "list", "-q", "quoted", "--folder", "Interviews", "--media", "text",
        "--source", "youtube", "--status", "completed", "--duplicates", "--sort",
        "published_desc", "--page", "2", "--page-size", "3",
    )
    human = _invoke(*args, "--long", "--facets")
    assert all(label in human for label in ("[仅文本]", "[待定人物]", "[摘要待更新]", "[x2]"))
    assert "One-line recap | graphs, systems" in human and "facets.media.text\t5" in human
    expected = {
        "page": 2, "page_size": 3, "search": "quoted", "media": "text", "source": "youtube",
        "status": "completed", "duplicates": True, "sort": "published_desc",
        "unclassified": False, "folder_id": "folder-1",
    }
    assert ("page", expected) in daemon.calls
    assert isinstance(json.loads(_invoke(*args, json_mode=True))["data"], list)
    assert json.loads(_invoke(*args, "--facets", json_mode=True))["data"]["total"] == 6


def test_archive_list_offline(library):
    _task, _directory = library
    folder = get_library_service().create("Empty")
    data = _invoke("archive", "list", "--folder", folder["id"], json_mode=True)
    assert json.loads(data)["data"] == []
    output = _invoke("archive", "list", "--unclassified", "-q", "quoted", "--media", "text")
    assert TITLE in output and "[摘要待更新]" in output and "[待定人物]" in output


def test_archive_edit_cue_and_conflict(daemon, tmp_path, monkeypatch):
    replacement = tmp_path / "replacement.md"
    replacement.write_text("Updated summary", encoding="utf-8")
    baseline = daemon.files["summary.md"]
    _invoke("archive", "edit", TITLE, "summary", "--from", str(replacement))
    assert ("write", "summary.md", baseline) in daemon.calls
    assert daemon.files["summary.md"] == "Updated summary"

    _invoke("archive", "cue", TITLE, "2", "--text", "Changed cue", "--speaker", "Carol")
    body = daemon.files["transcript_polished.srt"]
    assert "[Carol] Changed cue" in body and "[Alice] First line" in body
    assert ("write", "transcript_polished.srt", POLISHED) in daemon.calls
    _invoke("archive", "cue", TITLE, "2", "--delete", "--yes")
    updated = daemon.files["transcript_polished.srt"]
    assert "Changed cue" not in updated
    assert "2\n00:00:21,000" in updated

    real_write = daemon.fs_write

    def racing_write(path, content, **kwargs):
        daemon.files["summary.md"] = "Concurrent update"
        return real_write(path, content, **kwargs)

    replacement.write_text("Attempted overwrite", encoding="utf-8")
    monkeypatch.setattr(daemon, "fs_write", racing_write)
    error = _invoke("archive", "edit", TITLE, "summary", "--from", str(replacement),
                    json_mode=True, expected=4)
    assert json.loads(error)["error"]["code"] == "state_conflict"
    assert daemon.files["summary.md"] == "Concurrent update"


def test_archive_slim_uses_one_envelope(daemon):
    output = _invoke("archive", "slim", TITLE, "--apply", "--yes", "--json")
    result = json.loads(output)["data"]
    assert len(result["previews"]) == len(result["applied"]) == 1
    assert ("retention", daemon.root, "text", None) in daemon.calls
    assert ("retention", daemon.root, "text", ["original.mp4"]) in daemon.calls
    assert "keep" in _invoke("archive", "slim", "--all", "--policy", "playback")


def test_archive_cat_and_export(daemon, tmp_path):
    assert "One-line recap" in _invoke("archive", "cat", TITLE, "--file", "summary")
    assert "Alice" in _invoke("archive", "cat", TITLE, "--file", "speakers")
    output = tmp_path / "nested" / "bundle.zip"
    _invoke("archive", "export", TITLE, "--format", "zip", "-o", str(output))
    assert output.read_bytes() == b"portable zip bytes"
    assert ("zip", daemon.task_id, False) in daemon.calls
    _invoke("archive", "export", TITLE, "--file", "transcript", "-o", str(output))
    assert output.read_text() == POLISHED
    del daemon.files["transcript_polished.md"]
    _invoke("archive", "export", TITLE, "--format", "md", "-o", str(output))
    markdown = output.read_text()
    assert markdown.startswith("---\ntitle: ") and "**[Bob]** Graph search" in markdown
    _invoke("archive", "export", TITLE, "--format", "txt", "-o", str(output))
    assert "Graph search" in output.read_text() and "-->" not in output.read_text()


def test_archive_dedupe_reconcile_and_artifacts(daemon):
    assert TITLE in _invoke("archive", "dedupe")
    assert "Deleted\t1" in _invoke("archive", "dedupe", "--apply", "--yes")
    assert daemon.calls.count(("dedupe_apply",)) == 1
    assert "revision\t1" in _invoke("archive", "reconcile", "--status-only")
    assert "revision\t2" in _invoke("archive", "reconcile")
    assert "summary.md\tfile_missing" in _invoke("archive", "artifacts", TITLE)
    _invoke("archive", "artifacts", TITLE, "--repair", "mirror",
            "--names", "detail.md, summary.md", "--yes")
    assert ("repair", {"task_id": daemon.task_id, "action": "mirror", "filename": "detail.md"}) in (
        daemon.calls
    )
    _invoke("archive", "artifacts", TITLE, "--repair", "restore-file", "--yes")
    assert ("repair", {
        "task_id": daemon.task_id, "action": "restore-file", "filename": "summary.md",
    }) in daemon.calls


def test_client_filesystem_uses_existing_owner_and_optimistic_lock():
    task_id = str(uuid4())
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path, dict(request.url.params), request.content))
        return httpx.Response(200, json={"success": True})

    api = MppClient("http://localhost:18000")
    api.close()
    api._client = httpx.Client(base_url=api.base_url, transport=httpx.MockTransport(handler))
    try:
        api.fs_read("/foo.txt", task_id=task_id)
        api.fs_write("/foo.txt", "new", task_id=task_id, expected_content="")
    finally:
        api.close()
    assert calls[0][:3] == ("GET", "/api/filesystem/read", {"path": "/foo.txt", "task_id": task_id})
    assert calls[1][:2] == ("POST", "/api/filesystem/write")
    assert json.loads(calls[1][3]) == {
        "path": "/foo.txt", "content": "new", "task_id": task_id, "expected_content": "",
    }
