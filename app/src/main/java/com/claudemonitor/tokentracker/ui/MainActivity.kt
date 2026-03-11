package com.claudemonitor.tokentracker.ui

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.*
import androidx.lifecycle.viewmodel.compose.viewModel
import com.claudemonitor.tokentracker.ui.screens.DashboardScreen
import com.claudemonitor.tokentracker.ui.screens.DashboardViewModel
import com.claudemonitor.tokentracker.ui.screens.SettingsScreen
import com.claudemonitor.tokentracker.ui.theme.ClaudeTokenMonitorTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        setContent {
            val viewModel: DashboardViewModel = viewModel()
            val dashboardState by viewModel.dashboardState.collectAsState()
            val settingsState by viewModel.settingsState.collectAsState()
            var currentScreen by remember { mutableStateOf<Screen>(Screen.Dashboard) }

            ClaudeTokenMonitorTheme(darkTheme = settingsState.darkMode) {
                when (currentScreen) {
                    Screen.Dashboard -> {
                        DashboardScreen(
                            state = dashboardState,
                            settings = settingsState,
                            onRefresh = viewModel::refresh,
                            onNavigateToSettings = { currentScreen = Screen.Settings }
                        )
                    }
                    Screen.Settings -> {
                        SettingsScreen(
                            settings = settingsState,
                            onBack = { currentScreen = Screen.Dashboard },
                            onUpdateHost = viewModel::updateServerHost,
                            onUpdatePort = viewModel::updateServerPort,
                            onUpdateRefreshInterval = viewModel::updateRefreshInterval,
                            onUpdatePlan = viewModel::updatePlanType,
                            onUpdateDarkMode = viewModel::updateDarkMode,
                            onUpdateNotifications = viewModel::updateNotificationsEnabled,
                            onUpdateWarningThreshold = viewModel::updateWarningThreshold,
                            onUpdateCriticalThreshold = viewModel::updateCriticalThreshold
                        )
                    }
                }
            }
        }
    }
}

sealed class Screen {
    data object Dashboard : Screen()
    data object Settings : Screen()
}
