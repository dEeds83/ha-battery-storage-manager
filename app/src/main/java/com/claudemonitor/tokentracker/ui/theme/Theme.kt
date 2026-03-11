package com.claudemonitor.tokentracker.ui.theme

import android.app.Activity
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowCompat

// Claude brand-inspired colors
val ClaudeOrange = Color(0xFFD97706)
val ClaudeAmber = Color(0xFFF59E0B)
val ClaudeTeal = Color(0xFF0D9488)
val ClaudeRed = Color(0xFFEF4444)
val ClaudeGreen = Color(0xFF22C55E)

// Status colors
val StatusOk = Color(0xFF22C55E)
val StatusWarning = Color(0xFFF59E0B)
val StatusCritical = Color(0xFFEF4444)

// Chart colors
val ChartInput = Color(0xFF3B82F6)
val ChartOutput = Color(0xFFEC4899)
val ChartCacheRead = Color(0xFF8B5CF6)
val ChartCacheWrite = Color(0xFF06B6D4)

private val DarkColorScheme = darkColorScheme(
    primary = ClaudeOrange,
    secondary = ClaudeTeal,
    tertiary = ClaudeAmber,
    background = Color(0xFF0F172A),
    surface = Color(0xFF1E293B),
    surfaceVariant = Color(0xFF334155),
    onPrimary = Color.White,
    onSecondary = Color.White,
    onBackground = Color(0xFFF1F5F9),
    onSurface = Color(0xFFE2E8F0),
    onSurfaceVariant = Color(0xFF94A3B8),
    error = ClaudeRed,
    outline = Color(0xFF475569)
)

private val LightColorScheme = lightColorScheme(
    primary = ClaudeOrange,
    secondary = ClaudeTeal,
    tertiary = ClaudeAmber,
    background = Color(0xFFF8FAFC),
    surface = Color.White,
    surfaceVariant = Color(0xFFF1F5F9),
    onPrimary = Color.White,
    onSecondary = Color.White,
    onBackground = Color(0xFF0F172A),
    onSurface = Color(0xFF1E293B),
    onSurfaceVariant = Color(0xFF64748B),
    error = ClaudeRed,
    outline = Color(0xFFCBD5E1)
)

@Composable
fun ClaudeTokenMonitorTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit
) {
    val colorScheme = if (darkTheme) DarkColorScheme else LightColorScheme

    val view = LocalView.current
    if (!view.isInEditMode) {
        SideEffect {
            val window = (view.context as Activity).window
            window.statusBarColor = colorScheme.background.toArgb()
            WindowCompat.getInsetsController(window, view).isAppearanceLightStatusBars = !darkTheme
        }
    }

    MaterialTheme(
        colorScheme = colorScheme,
        typography = Typography(),
        content = content
    )
}
