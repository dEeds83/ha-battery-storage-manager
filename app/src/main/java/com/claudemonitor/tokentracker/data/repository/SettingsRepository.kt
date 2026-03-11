package com.claudemonitor.tokentracker.data.repository

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.*
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

private val Context.dataStore: DataStore<Preferences> by preferencesDataStore(name = "settings")

class SettingsRepository(private val context: Context) {

    companion object {
        val SERVER_HOST = stringPreferencesKey("server_host")
        val SERVER_PORT = intPreferencesKey("server_port")
        val REFRESH_INTERVAL = intPreferencesKey("refresh_interval_seconds")
        val PLAN_TYPE = stringPreferencesKey("plan_type")
        val DARK_MODE = booleanPreferencesKey("dark_mode")
        val NOTIFICATIONS_ENABLED = booleanPreferencesKey("notifications_enabled")
        val WARNING_THRESHOLD = intPreferencesKey("warning_threshold_percent")
        val CRITICAL_THRESHOLD = intPreferencesKey("critical_threshold_percent")

        const val DEFAULT_HOST = "192.168.1.100"
        const val DEFAULT_PORT = 5123
        const val DEFAULT_REFRESH_INTERVAL = 5
        const val DEFAULT_PLAN = "pro"
        const val DEFAULT_WARNING_THRESHOLD = 70
        const val DEFAULT_CRITICAL_THRESHOLD = 90
    }

    val serverHost: Flow<String> = context.dataStore.data.map { prefs ->
        prefs[SERVER_HOST] ?: DEFAULT_HOST
    }

    val serverPort: Flow<Int> = context.dataStore.data.map { prefs ->
        prefs[SERVER_PORT] ?: DEFAULT_PORT
    }

    val refreshInterval: Flow<Int> = context.dataStore.data.map { prefs ->
        prefs[REFRESH_INTERVAL] ?: DEFAULT_REFRESH_INTERVAL
    }

    val planType: Flow<String> = context.dataStore.data.map { prefs ->
        prefs[PLAN_TYPE] ?: DEFAULT_PLAN
    }

    val darkMode: Flow<Boolean> = context.dataStore.data.map { prefs ->
        prefs[DARK_MODE] ?: true
    }

    val notificationsEnabled: Flow<Boolean> = context.dataStore.data.map { prefs ->
        prefs[NOTIFICATIONS_ENABLED] ?: true
    }

    val warningThreshold: Flow<Int> = context.dataStore.data.map { prefs ->
        prefs[WARNING_THRESHOLD] ?: DEFAULT_WARNING_THRESHOLD
    }

    val criticalThreshold: Flow<Int> = context.dataStore.data.map { prefs ->
        prefs[CRITICAL_THRESHOLD] ?: DEFAULT_CRITICAL_THRESHOLD
    }

    suspend fun setServerHost(host: String) {
        context.dataStore.edit { it[SERVER_HOST] = host }
    }

    suspend fun setServerPort(port: Int) {
        context.dataStore.edit { it[SERVER_PORT] = port }
    }

    suspend fun setRefreshInterval(seconds: Int) {
        context.dataStore.edit { it[REFRESH_INTERVAL] = seconds }
    }

    suspend fun setPlanType(plan: String) {
        context.dataStore.edit { it[PLAN_TYPE] = plan }
    }

    suspend fun setDarkMode(enabled: Boolean) {
        context.dataStore.edit { it[DARK_MODE] = enabled }
    }

    suspend fun setNotificationsEnabled(enabled: Boolean) {
        context.dataStore.edit { it[NOTIFICATIONS_ENABLED] = enabled }
    }

    suspend fun setWarningThreshold(percent: Int) {
        context.dataStore.edit { it[WARNING_THRESHOLD] = percent }
    }

    suspend fun setCriticalThreshold(percent: Int) {
        context.dataStore.edit { it[CRITICAL_THRESHOLD] = percent }
    }

    fun getBaseUrl(host: String, port: Int): String {
        return "http://$host:$port"
    }
}
