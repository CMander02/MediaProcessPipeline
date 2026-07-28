package com.mpp.remote.data

import org.junit.Assert.assertEquals
import org.junit.Test

class ProcessingTargetTest {
    @Test
    fun storedValuesMatchServerContract() {
        assertEquals("server", ProcessingTarget.SERVER.storedValue)
        assertEquals("exe", ProcessingTarget.EXE.storedValue)
        ProcessingTarget.entries.forEach { target ->
            assertEquals(target, ProcessingTarget.fromStoredValue(target.storedValue))
        }
    }

    @Test
    fun missingOrUnknownValueDefaultsToServer() {
        assertEquals(ProcessingTarget.SERVER, ProcessingTarget.fromStoredValue(null))
        assertEquals(ProcessingTarget.SERVER, ProcessingTarget.fromStoredValue("unknown"))
    }
}
