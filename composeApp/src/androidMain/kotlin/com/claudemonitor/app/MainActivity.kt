package com.claudemonitor.app

import android.content.Context
import android.content.SharedPreferences
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import com.claudemonitor.app.data.AndroidLocalFileUsageDataSource
import com.claudemonitor.app.data.AndroidNetworkDataSource
import com.claudemonitor.app.data.UsageDataSource
import com.claudemonitor.app.model.DataMode
import com.claudemonitor.app.model.SettingsState
import com.claudemonitor.app.ui.ClaudeMonitorApp

class MainActivity : ComponentActivity() {

    private lateinit var prefs: SharedPreferences

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        prefs = getSharedPreferences("claude_monitor", Context.MODE_PRIVATE)
        val initialSettings = loadSettings()

        setContent {
            ClaudeMonitorApp(
                dataSource = createDataSource(initialSettings),
                initialSettings = initialSettings,
                isDesktop = false,
                onSettingsChanged = { settings -> saveSettings(settings) },
                onDataSourceChanged = { settings -> createDataSource(settings) }
            )
        }
    }

    private fun createDataSource(settings: SettingsState): UsageDataSource {
        return when (settings.dataMode) {
            DataMode.LOCAL -> AndroidLocalFileUsageDataSource(
                customPath = settings.claudePath
            )
            DataMode.REMOTE, DataMode.AUTO -> AndroidNetworkDataSource(
                host = settings.serverHost,
                port = settings.serverPort
            )
        }
    }

    private fun loadSettings(): SettingsState {
        return SettingsState(
            serverHost = prefs.getString("serverHost", "192.168.1.100") ?: "192.168.1.100",
            serverPort = prefs.getInt("serverPort", 5123),
            refreshInterval = prefs.getInt("refreshInterval", 5),
            planType = prefs.getString("planType", "pro") ?: "pro",
            darkMode = prefs.getBoolean("darkMode", true),
            notificationsEnabled = prefs.getBoolean("notifications", true),
            warningThreshold = prefs.getInt("warningThreshold", 70),
            criticalThreshold = prefs.getInt("criticalThreshold", 90),
            dataMode = try {
                DataMode.valueOf(prefs.getString("dataMode", "AUTO") ?: "AUTO")
            } catch (_: Exception) {
                DataMode.AUTO
            },
            claudePath = prefs.getString("claudePath", "") ?: "",
        )
    }

    private fun saveSettings(settings: SettingsState) {
        prefs.edit()
            .putString("serverHost", settings.serverHost)
            .putInt("serverPort", settings.serverPort)
            .putInt("refreshInterval", settings.refreshInterval)
            .putString("planType", settings.planType)
            .putBoolean("darkMode", settings.darkMode)
            .putBoolean("notifications", settings.notificationsEnabled)
            .putInt("warningThreshold", settings.warningThreshold)
            .putInt("criticalThreshold", settings.criticalThreshold)
            .putString("dataMode", settings.dataMode.name)
            .putString("claudePath", settings.claudePath)
            .apply()
    }
}
