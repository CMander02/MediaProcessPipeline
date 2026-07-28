package com.mpp.remote.ui

import com.mpp.remote.data.ArchiveItem
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Test

class MppRemoteUiStateTest {
    @Test
    fun compactPhoneWidthsUseTwoColumns() {
        assertEquals(
            ArchiveGridLayout.COMPACT_TWO_COLUMNS,
            archiveGridLayoutForWidth(390f),
        )
        assertEquals(
            ArchiveGridLayout.COMPACT_TWO_COLUMNS,
            archiveGridLayoutForWidth(430f),
        )
        assertEquals(
            ArchiveGridLayout.COMPACT_TWO_COLUMNS,
            archiveGridLayoutForWidth(599f),
        )
    }

    @Test
    fun tabletWidthsKeepAdaptiveLayout() {
        assertEquals(ArchiveGridLayout.ADAPTIVE, archiveGridLayoutForWidth(600f))
        assertEquals(ArchiveGridLayout.ADAPTIVE, archiveGridLayoutForWidth(768f))
        assertEquals(ArchiveGridLayout.ADAPTIVE, archiveGridLayoutForWidth(1024f))
    }

    @Test
    fun thumbnailRequestChangesWhenProcessingCompletes() {
        val processing = archive(processing = true)
        val completed = processing.copy(processing = false, hasImage = true)

        assertNotEquals(
            processing.thumbnailRequestKey(retryAttempt = 0),
            completed.thumbnailRequestKey(retryAttempt = 0),
        )
    }

    @Test
    fun thumbnailRequestChangesForManualRetry() {
        val archive = archive(processing = false)

        assertNotEquals(
            archive.thumbnailRequestKey(retryAttempt = 0),
            archive.thumbnailRequestKey(retryAttempt = 1),
        )
    }

    private fun archive(processing: Boolean): ArchiveItem =
        ArchiveItem(
            path = "/data/archive",
            title = "测试内容",
            processing = processing,
        )
}
