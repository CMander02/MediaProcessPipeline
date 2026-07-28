package com.mpp.remote.data

import org.junit.Assert.assertEquals
import org.junit.Test

class ThemeModeTest {
    @Test
    fun storedValuesRoundTrip() {
        ThemeMode.entries.forEach { mode ->
            assertEquals(mode, ThemeMode.fromStoredValue(mode.storedValue))
        }
    }

    @Test
    fun unknownValueFallsBackToSystem() {
        assertEquals(ThemeMode.SYSTEM, ThemeMode.fromStoredValue("unexpected"))
        assertEquals(ThemeMode.SYSTEM, ThemeMode.fromStoredValue(null))
    }
}
