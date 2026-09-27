from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.core import archive_sync, database, settings
from app.core.archive_index import source_key

RYAN = "https://www.bilibili.com/video/BV1Ab411c7Xy"


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(
        settings, "_runtime_settings", settings.RuntimeSettings(data_root=str(tmp_path))
    )
    database.reset_db_path(tmp_path)
    service = archive_sync.ArchiveSyncService()
    monkeypatch.setattr(archive_sync, "_sync_service", service)
    yield tmp_path, service
    database.close_db()


def add(root, title, *, status="completed", source_url=None, created="2026-01-01", platform="bilibili"):
    directory = root / title
    directory.mkdir()
    metadata = {
        "archive_id": str(uuid4()), "title": title, "status": status,
        "created_at": created, "platform": platform,
    }
    if source_url:
        metadata["source_url"] = source_url
    (directory / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    return directory


@pytest.fixture
def populated(library):
    root, service = library
    add(root, "Ryan done", source_url=RYAN, created="2026-01-03")
    add(root, "Ryan failed 1", status="failed", source_url=f"{RYAN}/?spm_id_from=333.1387", created="2026-01-02")
    add(root, "Ryan failed 2", status="failed", source_url=f"{RYAN}?p=1", created="2026-01-01")
    add(root, "Ryan part 2", source_url=f"{RYAN}?p=2", created="2026-01-04")
    add(root, "Solo failed", status="failed", source_url="https://www.youtube.com/watch?v=abc123", platform="youtube")
    add(root, "Paused", status="paused", source_url="https://www.bilibili.com/video/BV1Cd411e8Zw")
    add(root, "Local recording", platform="local")
    service.reconcile()
    return root, service


def titles(service, **query):
    return sorted(item["title"] for item in service.list_page(**query)["archives"])


def test_source_key_groups_repeated_links_but_not_other_parts():
    assert source_key(RYAN) == source_key(f"{RYAN}/?spm_id_from=333.1387") == source_key(f"{RYAN}?p=1")
    assert source_key(f"{RYAN}?p=2") != source_key(RYAN)
    assert source_key("https://youtu.be/abc123") == source_key("https://www.youtube.com/watch?v=abc123&t=30")
    assert source_key("C:\\videos\\talk.mp4") is None
    assert source_key("") is None


def test_repeated_attempts_collapse_to_the_best_one(populated):
    _root, service = populated
    page = service.list_page()
    assert page["total"] == 5
    ryan = next(item for item in page["archives"] if item["title"].startswith("Ryan done"))
    assert ryan["attempts"] == 3
    assert "Ryan failed 1" not in titles(service)


def test_status_filter_and_counts(populated):
    _root, service = populated
    facets = service.list_page()["facets"]
    assert facets["status"] == {
        # duplicates: cards the 重复 switch would list (every run of Ryan's video)
        "all": 5, "processing": 0, "paused": 1, "failed": 1, "completed": 3, "duplicates": 3,
    }
    assert titles(service, status="failed") == ["Solo failed"]
    assert titles(service, status="paused") == ["Paused"]
    # The library total ignores every filter, so "筛选后 1 / 共 5" stays honest.
    assert service.list_page(status="failed", search="solo")["facets"]["total"] == 5
    assert facets["source"]["bilibili"] == 3
    assert facets["source"]["youtube"] == 1
    assert facets["media"]["all"] == 5


def test_several_values_per_filter_are_combined_with_or(populated):
    _root, service = populated
    assert titles(service, status="failed,paused") == ["Paused", "Solo failed"]
    assert titles(service, status=["paused", "failed"]) == ["Paused", "Solo failed"]
    assert titles(service, source="youtube,local") == ["Local recording", "Solo failed"]
    # Filters still combine with AND across groups.
    assert titles(service, status="failed", source="bilibili") == []
    facets = service.list_page(status="failed", source="youtube,bilibili")["facets"]
    # Each group's counts ignore that group's own selection.
    assert facets["status"]["completed"] == 2
    assert facets["source"] == {"youtube": 1}


def test_duplicates_switch_combines_with_status(populated):
    _root, service = populated
    assert titles(service, duplicates=True, status="failed") == ["Ryan failed 1", "Ryan failed 2"]
    facets = service.list_page(status="failed")["facets"]
    assert facets["status"]["duplicates"] == 2  # failed runs among repeated sources


def test_unknown_filter_values_are_rejected(populated):
    _root, service = populated
    with pytest.raises(ValueError):
        service.list_page(status="broken")
    with pytest.raises(ValueError):
        service.list_page(source="myspace")


def test_duplicates_view_lists_every_attempt(populated):
    _root, service = populated
    page = service.list_page(status="duplicates")
    assert sorted(item["title"] for item in page["archives"]) == ["Ryan done", "Ryan failed 1", "Ryan failed 2"]
    assert all("attempts" not in item for item in page["archives"])


def test_cleanup_only_offers_failed_copies_of_finished_sources(populated):
    _root, service = populated
    candidates = sorted(item["title"] for item in service.duplicate_cleanup_candidates())
    assert candidates == ["Ryan failed 1", "Ryan failed 2"]


def test_lookup_finds_existing_archives_for_links(populated):
    _root, service = populated
    matches = service.lookup_sources([
        f"{RYAN}/?spm_id_from=333.999", "https://youtu.be/abc123", "https://example.com/new",
    ])
    assert sorted(item["title"] for item in matches[f"{RYAN}/?spm_id_from=333.999"]) == [
        "Ryan done", "Ryan failed 1", "Ryan failed 2",
    ]
    assert [item["title"] for item in matches["https://youtu.be/abc123"]] == ["Solo failed"]
    assert matches["https://youtu.be/abc123"][0]["status"] == "failed"
    assert "task_id" in matches["https://youtu.be/abc123"][0]
    assert matches["https://youtu.be/abc123"][0]["created_at"]
    assert "https://example.com/new" not in matches
