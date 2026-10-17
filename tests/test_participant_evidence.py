import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.core.model_router import EndpointBinding
from app.services.analysis.participant_evidence import frame_times, validate_participant_report
from app.services.analysis.vlm import VLMService


def test_frame_coverage_and_maximum():
    assert frame_times(1000) == [8, 500, 900]
    assert frame_times(1000, supplement=True, replace_intro=True) == [8, 500, 900, 45, 250, 750]
    assert len(frame_times(0.1, supplement=True)) == 1
    assert frame_times(float('nan')) == []


def test_reject_invented_quote_and_keep_visual_evidence_separate():
    result = validate_participant_report(json.dumps({
        "participants": [
            {"name": "Alice", "evidence": [{"source": "frame:8", "quote": "Host Alice"}]},
            {"name": "Bob", "evidence": [{"source": "metadata.description", "quote": "Guest Bob"}]},
        ], "count_estimate": 2,
    }), [{"id": "frame:8"}], {"metadata": {"description": "An interview"}})
    assert result["status"] == "partial"
    assert result["rejected_names"] == ["Bob"]
    assert result["participants"][0]["evidence"][0]["verification"] == "visual_observation"
    assert "mappings" not in result


def test_all_frames_sent_in_one_request(tmp_path, monkeypatch):
    from PIL import Image
    frames = []
    for i in range(6):
        path = tmp_path / f'{i}.png'
        Image.new('RGB', (50, 30), 'red').save(path)
        frames.append({"id": f"frame:{i}", "time": i, "path": str(path)})
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(
            content='{"participants":[],"count_estimate":null}'))])
    service = VLMService()
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(service, '_get_client', lambda binding: (client, 'test-vision'))
    binding = EndpointBinding(capability='vision', model='test-vision', api_base='http://localhost:1', api_key='', configured=True)
    report = service.describe_participants(frames, {"metadata": {"title": "访谈"}}, binding)
    assert len(calls) == 1
    content = calls[0]['messages'][1]['content']
    assert sum(x['type'] == 'image_url' for x in content) == 6
    assert len(report['images']) == 6
    with pytest.raises(ValueError, match='预算'):
        service.describe_participants(frames, {"evidence": 'x' * 12001}, binding)
    assert len(calls) == 1
