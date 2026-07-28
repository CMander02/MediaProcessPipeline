package com.mpp.remote.ui

import org.junit.Assert.assertEquals
import org.junit.Test

class ArchiveImageToolsTest {
    @Test
    fun sampleSizeKeepsDecodedImageWithinRequestedBound() {
        assertEquals(1, calculateInSampleSize(400, 300, 512))
        assertEquals(8, calculateInSampleSize(4_000, 3_000, 512))
        assertEquals(4, calculateInSampleSize(2_048, 1_024, 512))
    }

    @Test
    fun invalidDimensionsUseSafeDefault() {
        assertEquals(1, calculateInSampleSize(0, 1_000, 512))
        assertEquals(1, calculateInSampleSize(1_000, 1_000, 0))
    }

    @Test
    fun panBoundsUseFittedImageDimensions() {
        val bounds = archiveImagePanBounds(
            viewportWidth = 1_000,
            viewportHeight = 1_000,
            imageWidth = 2_000,
            imageHeight = 1_000,
            scale = 2f,
        )

        assertEquals(500f, bounds.horizontal, 0.001f)
        assertEquals(0f, bounds.vertical, 0.001f)
        assertEquals(
            ArchiveImagePan(x = 500f, y = 0f),
            clampArchiveImagePan(x = 900f, y = 100f, bounds = bounds),
        )
    }

    @Test
    fun zoomScaleIsBoundedAndDoubleTapTogglesReset() {
        assertEquals(5f, updatedArchiveImageScale(4f, 2f), 0.001f)
        assertEquals(1f, updatedArchiveImageScale(2f, 0.1f), 0.001f)
        assertEquals(2.5f, doubleTapArchiveImageScale(1f), 0.001f)
        assertEquals(1f, doubleTapArchiveImageScale(2.5f), 0.001f)
    }

    @Test
    fun galleryAndPreviewUsePurposeSizedServerRenditions() {
        assertEquals(512, ARCHIVE_GALLERY_IMAGE_MAX_EDGE)
        assertEquals(2_048, ARCHIVE_PREVIEW_IMAGE_MAX_EDGE)
    }
}
