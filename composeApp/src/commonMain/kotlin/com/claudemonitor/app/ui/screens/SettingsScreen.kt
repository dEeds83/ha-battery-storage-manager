package com.claudemonitor.app.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.claudemonitor.app.model.DataMode
import com.claudemonitor.app.model.SettingsState

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    settings: SettingsState,
    isDesktop: Boolean = false,
    onBack: () -> Unit,
    onUpdateSettings: (SettingsState) -> Unit,
) {
    var hostInput by remember(settings.serverHost) { mutableStateOf(settings.serverHost) }
    var portInput by remember(settings.serverPort) { mutableStateOf(settings.serverPort.toString()) }
    var intervalInput by remember(settings.refreshInterval) {
        mutableStateOf(settings.refreshInterval.toString())
    }
    var currentSettings by remember(settings) { mutableStateOf(settings) }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Settings", fontWeight = FontWeight.Bold) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.Default.ArrowBack, contentDescription = "Back")
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.background
                )
            )
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(24.dp)
        ) {
            // Data Mode Selection (Desktop shows local option)
            if (isDesktop) {
                Text(
                    "Data Source",
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.Bold,
                    color = MaterialTheme.colorScheme.primary
                )

                val modes = listOf(
                    DataMode.LOCAL to "Local Files",
                    DataMode.REMOTE to "Remote Server"
                )
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    modes.forEach { (mode, label) ->
                        FilterChip(
                            selected = currentSettings.dataMode == mode,
                            onClick = {
                                currentSettings = currentSettings.copy(dataMode = mode)
                                onUpdateSettings(currentSettings)
                            },
                            label = { Text(label) },
                            modifier = Modifier.weight(1f)
                        )
                    }
                }

                if (currentSettings.dataMode == DataMode.LOCAL) {
                    Card(
                        colors = CardDefaults.cardColors(
                            containerColor = MaterialTheme.colorScheme.surfaceVariant
                        )
                    ) {
                        Column(modifier = Modifier.padding(12.dp)) {
                            Text(
                                "Reading directly from ~/.claude/",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                            Text(
                                "No companion server needed on this machine.",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }
                    }
                }

                HorizontalDivider()
            }

            // Server Connection (show when remote mode or on Android)
            if (!isDesktop || currentSettings.dataMode == DataMode.REMOTE) {
                Text(
                    "Server Connection",
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.Bold,
                    color = MaterialTheme.colorScheme.primary
                )

                OutlinedTextField(
                    value = hostInput,
                    onValueChange = { hostInput = it },
                    label = { Text("Server Host / IP") },
                    placeholder = { Text("192.168.1.100") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth()
                )

                OutlinedTextField(
                    value = portInput,
                    onValueChange = { portInput = it },
                    label = { Text("Port") },
                    placeholder = { Text("5123") },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth()
                )

                Button(
                    onClick = {
                        currentSettings = currentSettings.copy(
                            serverHost = hostInput,
                            serverPort = portInput.toIntOrNull() ?: 5123
                        )
                        onUpdateSettings(currentSettings)
                    },
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Text("Save Connection")
                }

                HorizontalDivider()
            }

            // Monitoring
            Text(
                "Monitoring",
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.primary
            )

            OutlinedTextField(
                value = intervalInput,
                onValueChange = {
                    intervalInput = it
                    it.toIntOrNull()?.let { sec ->
                        currentSettings = currentSettings.copy(refreshInterval = sec)
                        onUpdateSettings(currentSettings)
                    }
                },
                label = { Text("Refresh Interval (seconds)") },
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                singleLine = true,
                modifier = Modifier.fillMaxWidth()
            )

            // Plan Selection
            Text(
                "Subscription Plan",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )

            val plans = listOf(
                "pro" to "Pro",
                "max5" to "Max 5x",
                "max20" to "Max 20x",
                "custom" to "Custom"
            )
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                plans.forEach { (value, label) ->
                    FilterChip(
                        selected = currentSettings.planType == value,
                        onClick = {
                            currentSettings = currentSettings.copy(planType = value)
                            onUpdateSettings(currentSettings)
                        },
                        label = { Text(label) },
                        modifier = Modifier.weight(1f)
                    )
                }
            }

            HorizontalDivider()

            // Alert Thresholds
            Text(
                "Alert Thresholds",
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.primary
            )

            Column {
                Text(
                    "Warning: ${currentSettings.warningThreshold}%",
                    style = MaterialTheme.typography.bodyMedium
                )
                Slider(
                    value = currentSettings.warningThreshold.toFloat(),
                    onValueChange = {
                        currentSettings = currentSettings.copy(warningThreshold = it.toInt())
                        onUpdateSettings(currentSettings)
                    },
                    valueRange = 50f..95f,
                    steps = 8
                )
            }

            Column {
                Text(
                    "Critical: ${currentSettings.criticalThreshold}%",
                    style = MaterialTheme.typography.bodyMedium
                )
                Slider(
                    value = currentSettings.criticalThreshold.toFloat(),
                    onValueChange = {
                        currentSettings = currentSettings.copy(criticalThreshold = it.toInt())
                        onUpdateSettings(currentSettings)
                    },
                    valueRange = 70f..99f,
                    steps = 5
                )
            }

            HorizontalDivider()

            // Preferences
            Text(
                "Preferences",
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.primary
            )

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text("Dark Mode", style = MaterialTheme.typography.bodyMedium)
                Switch(
                    checked = currentSettings.darkMode,
                    onCheckedChange = {
                        currentSettings = currentSettings.copy(darkMode = it)
                        onUpdateSettings(currentSettings)
                    }
                )
            }

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text("Usage Notifications", style = MaterialTheme.typography.bodyMedium)
                Switch(
                    checked = currentSettings.notificationsEnabled,
                    onCheckedChange = {
                        currentSettings = currentSettings.copy(notificationsEnabled = it)
                        onUpdateSettings(currentSettings)
                    }
                )
            }

            HorizontalDivider()

            // Setup Instructions
            Card(
                colors = CardDefaults.cardColors(
                    containerColor = MaterialTheme.colorScheme.surfaceVariant
                )
            ) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text(
                        "Setup Instructions",
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.Bold
                    )
                    Spacer(modifier = Modifier.height(8.dp))
                    if (isDesktop) {
                        Text(
                            "Local Mode (recommended for desktop):\n" +
                                    "  Data is read directly from ~/.claude/\n" +
                                    "  No additional setup needed.\n\n" +
                                    "Remote Mode:\n" +
                                    "  1. Run the companion server on a remote PC:\n" +
                                    "     python companion/claude_monitor_server.py\n" +
                                    "  2. Enter the remote PC's IP address above",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    } else {
                        Text(
                            "1. Run the companion server on your PC:\n" +
                                    "   python companion/claude_monitor_server.py\n\n" +
                                    "2. Enter your PC's local IP address above\n\n" +
                                    "3. Ensure both devices are on the same network\n\n" +
                                    "4. The server reads Claude Code usage data from\n" +
                                    "   ~/.claude/ and serves it via HTTP",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                }
            }

            Spacer(modifier = Modifier.height(32.dp))
        }
    }
}
