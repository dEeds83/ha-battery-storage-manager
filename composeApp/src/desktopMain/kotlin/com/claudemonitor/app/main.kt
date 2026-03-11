package com.claudemonitor.app

import androidx.compose.ui.Alignment
import androidx.compose.ui.unit.DpSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Window
import androidx.compose.ui.window.WindowPosition
import androidx.compose.ui.window.application
import androidx.compose.ui.window.rememberWindowState
import com.claudemonitor.app.data.LocalFileUsageDataSource
import com.claudemonitor.app.data.NetworkUsageDataSource
import com.claudemonitor.app.model.DataMode
import com.claudemonitor.app.model.SettingsState
import com.claudemonitor.app.server.EmbeddedUsageServer
import com.claudemonitor.app.ui.ClaudeMonitorApp
import java.io.File
import java.util.Properties

private var embeddedServer: EmbeddedUsageServer? = null

fun main() = application {
    val settingsFile = File(System.getProperty("user.home"), ".claude-monitor-settings.properties")
    val initialSettings = loadSettings(settingsFile)

    // Start embedded server if enabled
    if (initialSettings.embeddedServerEnabled) {
        startEmbeddedServer(initialSettings.embeddedServerPort)
    }

    val windowState = rememberWindowState(
        size = DpSize(480.dp, 900.dp),
        position = WindowPosition(Alignment.Center)
    )

    Window(
        onCloseRequest = {
            embeddedServer?.stop()
            exitApplication()
        },
        title = "Claude Token Monitor",
        state = windowState,
    ) {
        ClaudeMonitorApp(
            dataSource = createDataSource(initialSettings),
            initialSettings = initialSettings,
            isDesktop = true,
            onSettingsChanged = { settings ->
                saveSettings(settingsFile, settings)
                // Toggle embedded server based on settings
                if (settings.embeddedServerEnabled && embeddedServer == null) {
                    startEmbeddedServer(settings.embeddedServerPort)
                } else if (!settings.embeddedServerEnabled && embeddedServer != null) {
                    embeddedServer?.stop()
                    embeddedServer = null
                }
            },
            onDataSourceChanged = { settings -> createDataSource(settings) }
        )
    }
}

private fun startEmbeddedServer(port: Int) {
    try {
        embeddedServer?.stop()
        embeddedServer = EmbeddedUsageServer(port).also { it.start() }
    } catch (e: Exception) {
        System.err.println("Failed to start embedded server on port $port: ${e.message}")
    }
}

private fun createDataSource(settings: SettingsState): com.claudemonitor.app.data.UsageDataSource {
    return when (settings.dataMode) {
        DataMode.LOCAL, DataMode.AUTO -> LocalFileUsageDataSource()
        DataMode.REMOTE -> NetworkUsageDataSource(
            host = settings.serverHost,
            port = settings.serverPort
        )
    }
}

private fun loadSettings(file: File): SettingsState {
    if (!file.exists()) return SettingsState(dataMode = DataMode.LOCAL)

    return try {
        val props = Properties()
        file.inputStream().use { props.load(it) }
        SettingsState(
            serverHost = props.getProperty("serverHost", "192.168.1.100"),
            serverPort = props.getProperty("serverPort", "5123").toIntOrNull() ?: 5123,
            refreshInterval = props.getProperty("refreshInterval", "5").toIntOrNull() ?: 5,
            planType = props.getProperty("planType", "pro"),
            darkMode = props.getProperty("darkMode", "true").toBoolean(),
            notificationsEnabled = props.getProperty("notifications", "true").toBoolean(),
            warningThreshold = props.getProperty("warningThreshold", "70").toIntOrNull() ?: 70,
            criticalThreshold = props.getProperty("criticalThreshold", "90").toIntOrNull() ?: 90,
            dataMode = try {
                DataMode.valueOf(props.getProperty("dataMode", "LOCAL"))
            } catch (_: Exception) {
                DataMode.LOCAL
            },
            embeddedServerEnabled = props.getProperty("embeddedServerEnabled", "false").toBoolean(),
            embeddedServerPort = props.getProperty("embeddedServerPort", "5123").toIntOrNull() ?: 5123,
        )
    } catch (_: Exception) {
        SettingsState(dataMode = DataMode.LOCAL)
    }
}

private fun saveSettings(file: File, settings: SettingsState) {
    try {
        val props = Properties()
        props.setProperty("serverHost", settings.serverHost)
        props.setProperty("serverPort", settings.serverPort.toString())
        props.setProperty("refreshInterval", settings.refreshInterval.toString())
        props.setProperty("planType", settings.planType)
        props.setProperty("darkMode", settings.darkMode.toString())
        props.setProperty("notifications", settings.notificationsEnabled.toString())
        props.setProperty("warningThreshold", settings.warningThreshold.toString())
        props.setProperty("criticalThreshold", settings.criticalThreshold.toString())
        props.setProperty("dataMode", settings.dataMode.name)
        props.setProperty("embeddedServerEnabled", settings.embeddedServerEnabled.toString())
        props.setProperty("embeddedServerPort", settings.embeddedServerPort.toString())
        file.outputStream().use { props.store(it, "Claude Token Monitor Settings") }
    } catch (_: Exception) {
        // Silently fail on settings save errors
    }
}
