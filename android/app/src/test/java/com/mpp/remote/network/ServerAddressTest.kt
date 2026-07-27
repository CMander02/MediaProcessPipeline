package com.mpp.remote.network

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class ServerAddressTest {
    @Test
    fun normalizesTrailingSlash() {
        assertEquals(
            "https://mpp.example.com",
            normalizeServerUrl(" https://mpp.example.com/ "),
        )
    }

    @Test
    fun preservesReverseProxyPath() {
        assertEquals(
            "https://example.com/mpp/api/tasks",
            buildEndpoint("https://example.com/mpp/", "/api/tasks"),
        )
    }

    @Test
    fun rejectsAddressWithoutHttpScheme() {
        assertThrows(IllegalArgumentException::class.java) {
            normalizeServerUrl("mpp.example.com")
        }
    }
}
