package com.claudemonitor.tokentracker.ui.screens

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.claudemonitor.tokentracker.data.model.DashboardState
import com.claudemonitor.tokentracker.data.network.NetworkModule
import com.claudemonitor.tokentracker.data.repository.SettingsRepository
import com.claudemonitor.tokentracker.data.repository.UsageRepository
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch

class DashboardViewModel(application: Application) : AndroidViewModel(application) {

    private val settingsRepository = SettingsRepository(application)

    private val _dashboardState = MutableStateFlow(DashboardState())
    val dashboardState: StateFlow<DashboardState> = _dashboardState.asStateFlow()

    private val _settingsState = MutableStateFlow(SettingsState())
    val settingsState: StateFlow<SettingsState> = _settingsState.asStateFlow()

    private var pollingJob: Job? = null
    private var usageRepository: UsageRepository? = null

    init {
        observeSettings()
    }

    private fun observeSettings() {
        viewModelScope.launch {
            combine(
                settingsRepository.serverHost,
                settingsRepository.serverPort,
                settingsRepository.refreshInterval,
                settingsRepository.planType,
                settingsRepository.darkMode,
                settingsRepository.notificationsEnabled,
                settingsRepository.warningThreshold,
                settingsRepository.criticalThreshold
            ) { values ->
                SettingsState(
                    serverHost = values[0] as String,
                    serverPort = values[1] as Int,
                    refreshInterval = values[2] as Int,
                    planType = values[3] as String,
                    darkMode = values[4] as Boolean,
                    notificationsEnabled = values[5] as Boolean,
                    warningThreshold = values[6] as Int,
                    criticalThreshold = values[7] as Int
                )
            }.collect { settings ->
                _settingsState.value = settings
                reconnect(settings)
            }
        }
    }

    private fun reconnect(settings: SettingsState) {
        pollingJob?.cancel()
        val baseUrl = settingsRepository.getBaseUrl(settings.serverHost, settings.serverPort)
        val apiService = NetworkModule.createApiService(baseUrl)
        usageRepository = UsageRepository(apiService)
        startPolling(settings.refreshInterval, settings.planType)
    }

    private fun startPolling(intervalSeconds: Int, plan: String) {
        pollingJob = viewModelScope.launch {
            while (true) {
                fetchData(plan)
                delay(intervalSeconds * 1000L)
            }
        }
    }

    private suspend fun fetchData(plan: String) {
        val repo = usageRepository ?: return
        val result = repo.fetchUsage(hours = 168, plan = plan)
        result.fold(
            onSuccess = { state ->
                _dashboardState.value = state
            },
            onFailure = { error ->
                _dashboardState.value = _dashboardState.value.copy(
                    isConnected = false,
                    isLoading = false,
                    errorMessage = error.message ?: "Connection failed"
                )
            }
        )
    }

    fun refresh() {
        viewModelScope.launch {
            _dashboardState.value = _dashboardState.value.copy(isLoading = true)
            fetchData(_settingsState.value.planType)
        }
    }

    fun updateServerHost(host: String) {
        viewModelScope.launch { settingsRepository.setServerHost(host) }
    }

    fun updateServerPort(port: Int) {
        viewModelScope.launch { settingsRepository.setServerPort(port) }
    }

    fun updateRefreshInterval(seconds: Int) {
        viewModelScope.launch { settingsRepository.setRefreshInterval(seconds) }
    }

    fun updatePlanType(plan: String) {
        viewModelScope.launch { settingsRepository.setPlanType(plan) }
    }

    fun updateDarkMode(enabled: Boolean) {
        viewModelScope.launch { settingsRepository.setDarkMode(enabled) }
    }

    fun updateNotificationsEnabled(enabled: Boolean) {
        viewModelScope.launch { settingsRepository.setNotificationsEnabled(enabled) }
    }

    fun updateWarningThreshold(percent: Int) {
        viewModelScope.launch { settingsRepository.setWarningThreshold(percent) }
    }

    fun updateCriticalThreshold(percent: Int) {
        viewModelScope.launch { settingsRepository.setCriticalThreshold(percent) }
    }

    override fun onCleared() {
        super.onCleared()
        pollingJob?.cancel()
    }
}

data class SettingsState(
    val serverHost: String = SettingsRepository.DEFAULT_HOST,
    val serverPort: Int = SettingsRepository.DEFAULT_PORT,
    val refreshInterval: Int = SettingsRepository.DEFAULT_REFRESH_INTERVAL,
    val planType: String = SettingsRepository.DEFAULT_PLAN,
    val darkMode: Boolean = true,
    val notificationsEnabled: Boolean = true,
    val warningThreshold: Int = SettingsRepository.DEFAULT_WARNING_THRESHOLD,
    val criticalThreshold: Int = SettingsRepository.DEFAULT_CRITICAL_THRESHOLD
)
