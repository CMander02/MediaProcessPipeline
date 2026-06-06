from app.services.analysis.llm import merge_consecutive_speaker_segments, srt_to_markdown


def test_merge_consecutive_speaker_segments_merges_same_speaker_runs():
    srt = """1
00:00:00,000 --> 00:00:01,000
[SPEAKER_00] 我觉得在

2
00:00:01,200 --> 00:00:02,000
[SPEAKER_00]

3
00:00:02,100 --> 00:00:03,000
[SPEAKER_00] 可能去年之前，

4
00:00:03,500 --> 00:00:04,000
[SPEAKER_01] 为什么？

5
00:00:04,200 --> 00:00:05,000
[SPEAKER_01] 我再问一个。
"""

    merged = merge_consecutive_speaker_segments(srt)

    assert merged == """1
00:00:00,000 --> 00:00:03,000
[SPEAKER_00] 我觉得在可能去年之前，

2
00:00:03,500 --> 00:00:05,000
[SPEAKER_01] 为什么？我再问一个。"""


def test_merge_consecutive_speaker_segments_does_not_merge_unlabeled_text():
    srt = """1
00:00:00,000 --> 00:00:01,000
[SPEAKER_00] 有标签。

2
00:00:01,200 --> 00:00:02,000
没有标签。

3
00:00:02,100 --> 00:00:03,000
[SPEAKER_00] 再次有标签。
"""

    merged = merge_consecutive_speaker_segments(srt)

    assert merged == """1
00:00:00,000 --> 00:00:01,000
[SPEAKER_00] 有标签。

2
00:00:01,200 --> 00:00:02,000
没有标签。

3
00:00:02,100 --> 00:00:03,000
[SPEAKER_00] 再次有标签。"""


def test_merge_consecutive_speaker_segments_splits_long_same_speaker_runs():
    srt = """1
00:00:00,000 --> 00:00:01,000
[SPEAKER_00] 第一句。

2
00:00:01,000 --> 00:00:02,000
[SPEAKER_00] 第二句。

3
00:00:02,000 --> 00:00:03,000
[SPEAKER_00] 第三句。

4
00:00:03,000 --> 00:00:04,000
[SPEAKER_00] 第四句。

5
00:00:04,000 --> 00:00:05,000
[SPEAKER_00] 第五句。
"""

    merged = merge_consecutive_speaker_segments(
        srt,
        max_sentences=2,
        max_chars=1000,
        max_duration=999,
    )

    assert merged == """1
00:00:00,000 --> 00:00:02,000
[SPEAKER_00] 第一句。第二句。

2
00:00:02,000 --> 00:00:04,000
[SPEAKER_00] 第三句。第四句。

3
00:00:04,000 --> 00:00:05,000
[SPEAKER_00] 第五句。"""


def test_merge_consecutive_speaker_segments_splits_english_speaker_blocks():
    srt = """1
00:00:00,000 --> 00:00:05,000
[Andrew Mayne] First sentence. Second sentence.

2
00:00:05,000 --> 00:00:10,000
[Andrew Mayne] Third sentence. Fourth sentence.

3
00:00:10,000 --> 00:00:14,000
[Alexander Wei] GPT-3.5 was part of the story. This should stay readable.

4
00:00:14,000 --> 00:00:16,000
[Lijie Chen] And then I was like, “Oh, that’s crazy.”

5
00:00:16,000 --> 00:00:18,000
[Lijie Chen] You know, models can already win medals.
"""

    merged = merge_consecutive_speaker_segments(
        srt,
        max_sentences=2,
        max_chars=160,
        max_duration=999,
    )

    assert merged == """1
00:00:00,000 --> 00:00:05,000
[Andrew Mayne] First sentence. Second sentence.

2
00:00:05,000 --> 00:00:10,000
[Andrew Mayne] Third sentence. Fourth sentence.

3
00:00:10,000 --> 00:00:14,000
[Alexander Wei] GPT-3.5 was part of the story. This should stay readable.

4
00:00:14,000 --> 00:00:18,000
[Lijie Chen] And then I was like, “Oh, that’s crazy.” You know, models can already win medals."""


def test_srt_to_markdown_preserves_turn_boundaries():
    srt = """1
00:00:00,000 --> 00:00:02,000
[SPEAKER_00] 第一段。

2
00:00:02,000 --> 00:00:04,000
[SPEAKER_00] 第二段。

3
00:00:04,000 --> 00:00:05,000
[SPEAKER_01] 第三段。
"""

    markdown = srt_to_markdown(srt, "标题")

    assert "**[SPEAKER_00]** 第一段。\n\n**[SPEAKER_00]** 第二段。" in markdown
