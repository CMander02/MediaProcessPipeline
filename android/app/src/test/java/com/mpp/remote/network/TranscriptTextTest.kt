package com.mpp.remote.network

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class TranscriptTextTest {
    @Test
    fun removesSrtSequenceNumbersAndTimestamps() {
        val raw = """
            1
            00:00:01,000 --> 00:00:03,000
            第一段

            2
            00:00:03.100 --> 00:00:05.000
            第二段
        """.trimIndent()

        assertEquals("第一段\n第二段", srtToReadableText(raw))
    }

    @Test
    fun parsesCrLfDotMillisecondsAndSpeaker() {
        val raw = "\uFEFF1\r\n" +
            "00:00:01.250 --> 00:01:02,500\r\n" +
            "[SPEAKER_01] 第一行\r\n第二行\r\n"

        val cues = parseSrt(raw)

        assertEquals(1, cues.size)
        assertEquals(1_250L, cues.single().startTimeMs)
        assertEquals(62_500L, cues.single().endTimeMs)
        assertEquals("SPEAKER_01", cues.single().speaker)
        assertEquals("第一行\n第二行", cues.single().text)
        assertTrue(srtToReadableText(raw).startsWith("SPEAKER_01："))
    }

    @Test
    fun formatsSubtitleTimesForShortAndLongMedia() {
        assertEquals("00:09", formatSubtitleTime(9_999L))
        assertEquals("01:02", formatSubtitleTime(62_000L))
        assertEquals("1:01:02", formatSubtitleTime(3_662_000L))
    }
}
