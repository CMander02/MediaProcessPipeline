from app.services.recognition.alignment import _restore_source_text
from app.services.recognition.sherpa_onnx_asr import _merge_timed_units
import pytest


def test_restores_sentence_punctuation_without_changing_acoustic_times():
    units = [dict(text=t, start=i * .5, end=(i + 1) * .5) for i, t in enumerate(['你','好','GPT','3','5','好'])]
    restored = _restore_source_text(units, '你好！GPT-3.5 好。')
    assert [u['text'] for u in restored] == ['你','好！','GPT-','3.','5','好。']
    assert [(u['start'],u['end']) for u in restored] == [(u['start'],u['end']) for u in units]
    merged = _merge_timed_units(restored, offset=10)
    assert merged[0] == dict(start=10, end=11, text='你好！')
    assert merged[-1]['end'] == 13


def test_alignment_token_mismatch_is_reported():
    with pytest.raises(RuntimeError, match='tokens do not match'):
        _restore_source_text([dict(text='错', start=0, end=1)], '好。')


def test_zero_duration_acknowledgement_joins_following_aligned_sentence():
    result = _merge_timed_units([
        dict(text='对。', start=0, end=0),
        dict(text='我们继续。', start=0, end=2),
    ], offset=10, end_bound=13)
    assert result == [dict(text='对。我们继续。', start=10, end=12)]


def test_zero_duration_only_utterance_uses_acoustic_bounds():
    assert _merge_timed_units([dict(text='嗯。', start=0, end=0)],
                             offset=10, end_bound=10.7) == [
        dict(text='嗯。', start=10, end=10.7)
    ]


def test_trailing_zero_duration_text_is_preserved_with_previous_sentence():
    result = _merge_timed_units([
        dict(text='我们继续。', start=0, end=2),
        dict(text='好。', start=2, end=2),
    ], offset=10, end_bound=12.5)
    assert result == [dict(text='我们继续。好。', start=10, end=12)]
