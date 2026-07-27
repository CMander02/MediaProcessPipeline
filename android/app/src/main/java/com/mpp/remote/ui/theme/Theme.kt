package com.mpp.remote.ui.theme

import android.os.Build
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext

private val LightColors = lightColorScheme(
    primary = Color(0xFF136F63),
    onPrimary = Color.White,
    primaryContainer = Color(0xFFA7F2E2),
    onPrimaryContainer = Color(0xFF00201C),
    secondary = Color(0xFF4B635E),
    background = Color(0xFFF7FAF8),
    surface = Color(0xFFF7FAF8),
)

private val DarkColors = darkColorScheme(
    primary = Color(0xFF8AD6C7),
    onPrimary = Color(0xFF003730),
    primaryContainer = Color(0xFF005047),
    onPrimaryContainer = Color(0xFFA7F2E2),
    secondary = Color(0xFFB2CCC5),
)

@Composable
fun MppRemoteTheme(
    darkTheme: Boolean,
    content: @Composable () -> Unit,
) {
    val context = LocalContext.current
    val colors = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
        if (darkTheme) dynamicDarkColorScheme(context) else dynamicLightColorScheme(context)
    } else if (darkTheme) {
        DarkColors
    } else {
        LightColors
    }

    MaterialTheme(
        colorScheme = colors,
        content = content,
    )
}
