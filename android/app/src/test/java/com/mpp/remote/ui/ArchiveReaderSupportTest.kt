package com.mpp.remote.ui

import com.mpp.remote.data.ArchiveContent
import com.mpp.remote.data.ArchiveDocument
import com.mpp.remote.data.ArchiveItem
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ArchiveReaderSupportTest {
    private val archive = ArchiveItem(
        path = "/data/item",
        title = "标题：一/二?",
    )

    @Test
    fun originalSubtitleExportsAsSrtWithoutLosingTimestamps() {
        val srt = """
            1
            00:00:01,000 --> 00:00:03,000
            字幕
        """.trimIndent()
        val export = archiveExportDocument(
            ArchiveContent(archive = archive, transcriptSrt = srt),
            ArchiveDocument.TRANSCRIPT,
        )

        assertTrue(export.fileName.endsWith("-字幕.srt"))
        assertEquals("application/x-subrip", export.mimeType)
        assertEquals(srt, export.content)
    }

    @Test
    fun markdownDocumentUsesSafeFilename() {
        val export = archiveExportDocument(
            ArchiveContent(archive = archive, summary = "# 摘要"),
            ArchiveDocument.SUMMARY,
        )

        assertTrue(export.fileName.endsWith("-摘要.md"))
        assertFalse(export.fileName.contains('/'))
        assertFalse(export.fileName.contains('?'))
        assertEquals("text/markdown", export.mimeType)
    }

    @Test
    fun clientLabelsUseReaderFacingNames() {
        assertEquals("Android", clientLabel("android"))
        assertEquals("EXE", clientLabel("exe"))
        assertEquals("Server", clientLabel("server"))
    }
}
