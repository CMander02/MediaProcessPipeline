package com.mpp.remote.ui.theme

import androidx.compose.ui.graphics.Color
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Test

class ThemeTest {
    @Test
    fun lightThemeUsesFixedBluePalette() {
        val colors = mppColorScheme(darkTheme = false)

        assertEquals(Color(0xFF2563EB), colors.primary)
        assertEquals(Color(0xFFF8FAFC), colors.background)
        assertEquals(Color(0xFFDBEAFE), colors.primaryContainer)
    }

    @Test
    fun darkThemeUsesFixedDeepBluePalette() {
        val colors = mppColorScheme(darkTheme = true)

        assertEquals(Color(0xFF93C5FD), colors.primary)
        assertEquals(Color(0xFF07111F), colors.background)
        assertEquals(Color(0xFF1E3A8A), colors.primaryContainer)
        assertNotEquals(MppLightColors.background, colors.background)
    }
}
