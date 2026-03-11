package com.claudemonitor.tokentracker.ui.screens

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

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    settings: SettingsState,
    onBack: () -> Unit,
    onUpdateHost: (String) -> Unit,
    onUpdatePort: (Int) -> Unit,
    onUpdateRefreshInterval: (Int) -> Unit,
    onUpdatePlan: (String) -> Unit,
    onUpdateDarkMode: (Boolean) -> Unit,
    onUpdateNotifications: (Boolean) -> Unit,
    onUpdateWarningThreshold: (Int) -> Unit,
    onUpdateCriticalThreshold: (Int) -> Unit
) {
    var hostInput by remember(settings.serverHost) { mutableStateOf(settings.serverHost) }
    var portInput by remember(settings.serverPort) { mutableStateOf(settings.serverPort.toString()) }
    var intervalInput by remember(settings.refreshInterval) {
        mutableStateOf(settings.refreshInterval.toString())
    }

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
            // Server Connection
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
                    onUpdateHost(hostInput)
                    portInput.toIntOrNull()?.let { onUpdatePort(it) }
                },
                modifier = Modifier.fillMaxWidth()
            ) {
                Text("Save Connection")
            }

            HorizontalDivider()

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
                    it.toIntOrNull()?.let { sec -> onUpdateRefreshInterval(sec) }
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

            val plans = listOf("pro" to "Pro", "max5" to "Max 5x", "max20" to "Max 20x", "custom" to "Custom")
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                plans.forEach { (value, label) ->
                    FilterChip(
                        selected = settings.planType == value,
                        onClick = { onUpdatePlan(value) },
                        label = { Text(label) },
                        modifier = Modifier.weight(1f)
                    )
                }
            }

            HorizontalDivider()

            // Thresholds
            Text(
                "Alert Thresholds",
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.primary
            )

            Column {
                Text(
                    "Warning: ${settings.warningThreshold}%",
                    style = MaterialTheme.typography.bodyMedium
                )
                Slider(
                    value = settings.warningThreshold.toFloat(),
                    onValueChange = { onUpdateWarningThreshold(it.toInt()) },
                    valueRange = 50f..95f,
                    steps = 8
                )
            }

            Column {
                Text(
                    "Critical: ${settings.criticalThreshold}%",
                    style = MaterialTheme.typography.bodyMedium
                )
                Slider(
                    value = settings.criticalThreshold.toFloat(),
                    onValueChange = { onUpdateCriticalThreshold(it.toInt()) },
                    valueRange = 70f..99f,
                    steps = 5
                )
            }

            HorizontalDivider()

            // Appearance & Notifications
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
                    checked = settings.darkMode,
                    onCheckedChange = onUpdateDarkMode
                )
            }

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text("Usage Notifications", style = MaterialTheme.typography.bodyMedium)
                Switch(
                    checked = settings.notificationsEnabled,
                    onCheckedChange = onUpdateNotifications
                )
            }

            HorizontalDivider()

            // Info
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

            Spacer(modifier = Modifier.height(32.dp))
        }
    }
}
