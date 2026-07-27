package com.mpp.remote.network

import org.junit.Assert.assertEquals
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
}
