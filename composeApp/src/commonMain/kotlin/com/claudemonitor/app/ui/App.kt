package com.claudemonitor.app.ui

import androidx.compose.runtime.*
import com.claudemonitor.app.data.UsageDataSource
import com.claudemonitor.app.model.DashboardState
import com.claudemonitor.app.model.SettingsState
import com.claudemonitor.app.ui.screens.DashboardScreen
import com.claudemonitor.app.ui.screens.SettingsScreen
import com.claudemonitor.app.ui.theme.ClaudeTokenMonitorTheme
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive

enum class Screen { Dashboard, Settings }

@Composable
fun ClaudeMonitorApp(
    dataSource: UsageDataSource,
    initialSettings: SettingsState = SettingsState(),
    isDesktop: Boolean = false,
    onSettingsChanged: (SettingsState) -> Unit = {},
    onDataSourceChanged: (SettingsState) -> UsageDataSource? = { null },
) {
    var currentScreen by remember { mutableStateOf(Screen.Dashboard) }
    var settings by remember { mutableStateOf(initialSettings) }
    var dashboardState by remember { mutableStateOf(DashboardState()) }
    var activeDataSource by remember { mutableStateOf(dataSource) }

    // Polling loop
    LaunchedEffect(activeDataSource, settings.refreshInterval, settings.planType) {
        while (isActive) {
            val result = activeDataSource.fetchDashboardState(
                hours = 168,
                plan = settings.planType
            )
            result.fold(
                onSuccess = { state -> dashboardState = state },
                onFailure = { error ->
                    dashboardState = dashboardState.copy(
                        isConnected = false,
                        isLoading = false,
                        errorMessage = error.message ?: "Connection failed"
                    )
                }
            )
            delay(settings.refreshInterval * 1000L)
        }
    }

    ClaudeTokenMonitorTheme(darkTheme = settings.darkMode) {
        when (currentScreen) {
            Screen.Dashboard -> {
                DashboardScreen(
                    state = dashboardState,
                    settings = settings,
                    onRefresh = {
                        dashboardState = dashboardState.copy(isLoading = true)
                    },
                    onNavigateToSettings = { currentScreen = Screen.Settings }
                )
            }

            Screen.Settings -> {
                SettingsScreen(
                    settings = settings,
                    isDesktop = isDesktop,
                    onBack = { currentScreen = Screen.Dashboard },
                    onUpdateSettings = { newSettings ->
                        val oldSettings = settings
                        settings = newSettings
                        onSettingsChanged(newSettings)

                        // If connection params changed, create new data source
                        if (newSettings.serverHost != oldSettings.serverHost ||
                            newSettings.serverPort != oldSettings.serverPort ||
                            newSettings.dataMode != oldSettings.dataMode
                        ) {
                            onDataSourceChanged(newSettings)?.let {
                                activeDataSource = it
                            }
                        }
                    },
                )
            }
        }
    }
}
