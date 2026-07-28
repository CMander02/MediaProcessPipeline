package com.mpp.remote.network

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class ArchiveImageRequestTest {
    @Test
    fun requestUsesServerSideArchiveImageResizeEndpoint() {
        assertEquals(
            "/api/pipeline/archives/image" +
                "?path=D%3A%5CMedia%20Library%5Cimages%5C01.png&max_edge=512",
            archiveImageRequestPath(
                path = """D:\Media Library\images\01.png""",
                maxEdge = 512,
            ),
        )
    }

    @Test
    fun requestRejectsUnsafeResizeBounds() {
        assertThrows(IllegalArgumentException::class.java) {
            archiveImageRequestPath("/data/archive/image.png", 128)
        }
        assertThrows(IllegalArgumentException::class.java) {
            archiveImageRequestPath("/data/archive/image.png", 8192)
        }
    }
}
