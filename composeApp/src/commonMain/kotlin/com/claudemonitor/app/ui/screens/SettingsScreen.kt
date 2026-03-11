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
    var claudePathInput by remember(settings.claudePath) { mutableStateOf(settings.claudePath) }
    var embeddedPortInput by remember(settings.embeddedServerPort) {
        mutableStateOf(settings.embeddedServerPort.toString())
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
            // Data Mode Selection (shown on both Desktop and Android)
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
                        if (isDesktop) {
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
                        } else {
                            Text(
                                "Reading .claude/ files directly from this device.",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                            Text(
                                "Works with Termux or synced .claude/ folders.",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }
                    }
                }

                // Show path configuration for Android local mode
                if (!isDesktop) {
                    OutlinedTextField(
                        value = claudePathInput,
                        onValueChange = { claudePathInput = it },
                        label = { Text("Claude Data Path (optional)") },
                        placeholder = { Text("/data/data/com.termux/files/home/.claude") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                        supportingText = {
                            Text("Leave empty for auto-detection (Termux, shared storage)")
                        }
                    )

                    Button(
                        onClick = {
                            currentSettings = currentSettings.copy(claudePath = claudePathInput)
                            onUpdateSettings(currentSettings)
                        },
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Text("Save Path")
                    }
                }
            }

            HorizontalDivider()

            // Server Connection (show when remote mode)
            if (currentSettings.dataMode == DataMode.REMOTE) {
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

            // Embedded Server (Desktop only) - serves data to Android clients
            if (isDesktop) {
                Text(
                    "Embedded Server",
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.Bold,
                    color = MaterialTheme.colorScheme.primary
                )

                Card(
                    colors = CardDefaults.cardColors(
                        containerColor = MaterialTheme.colorScheme.surfaceVariant
                    )
                ) {
                    Column(modifier = Modifier.padding(12.dp)) {
                        Text(
                            "Enable the embedded HTTP server so Android devices " +
                                    "can connect directly to this Desktop app. " +
                                    "No separate Python companion server needed.",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                }

                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text("Enable Embedded Server", style = MaterialTheme.typography.bodyMedium)
                    Switch(
                        checked = currentSettings.embeddedServerEnabled,
                        onCheckedChange = {
                            currentSettings = currentSettings.copy(embeddedServerEnabled = it)
                            onUpdateSettings(currentSettings)
                        }
                    )
                }

                if (currentSettings.embeddedServerEnabled) {
                    OutlinedTextField(
                        value = embeddedPortInput,
                        onValueChange = { embeddedPortInput = it },
                        label = { Text("Server Port") },
                        placeholder = { Text("5123") },
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth()
                    )

                    Button(
                        onClick = {
                            currentSettings = currentSettings.copy(
                                embeddedServerPort = embeddedPortInput.toIntOrNull() ?: 5123
                            )
                            onUpdateSettings(currentSettings)
                        },
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Text("Apply Port")
                    }
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
                                    "Embedded Server:\n" +
                                    "  Enable the embedded server above so that\n" +
                                    "  Android devices on your network can connect\n" +
                                    "  directly to this Desktop app.\n\n" +
                                    "Remote Mode:\n" +
                                    "  Connect to another machine running the\n" +
                                    "  companion server or Desktop app with\n" +
                                    "  embedded server enabled.",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    } else {
                        Text(
                            "Local Mode (Termux / on-device):\n" +
                                    "  Reads .claude/ files directly from this device.\n" +
                                    "  Works with Termux or synced directories.\n" +
                                    "  Set custom path above if needed.\n\n" +
                                    "Remote Mode:\n" +
                                    "  Connect to the Desktop app (with embedded\n" +
                                    "  server enabled) or the standalone companion\n" +
                                    "  server on your PC.\n\n" +
                                    "  1. On your PC: open the Desktop app and\n" +
                                    "     enable 'Embedded Server' in settings\n" +
                                    "  2. Enter your PC's local IP address above\n" +
                                    "  3. Both devices must be on the same network",
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
