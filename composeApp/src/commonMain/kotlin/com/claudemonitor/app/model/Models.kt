package com.claudemonitor.app.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * A single token usage record from Claude Code.
 */
@Serializable
data class TokenUsageRecord(
    val timestamp: Long = 0L,
    @SerialName("input_tokens") val inputTokens: Long = 0,
    @SerialName("output_tokens") val outputTokens: Long = 0,
    @SerialName("cache_read_tokens") val cacheReadTokens: Long = 0,
    @SerialName("cache_write_tokens") val cacheWriteTokens: Long = 0,
    val model: String = "",
    @SerialName("session_id") val sessionId: String = "",
    val cost: Double = 0.0
) {
    val totalTokens: Long get() = inputTokens + outputTokens + cacheReadTokens + cacheWriteTokens
}

/**
 * Aggregated usage summary for a time period.
 */
data class UsageSummary(
    val periodLabel: String = "",
    val totalInputTokens: Long = 0,
    val totalOutputTokens: Long = 0,
    val totalCacheReadTokens: Long = 0,
    val totalCacheWriteTokens: Long = 0,
    val totalCost: Double = 0.0,
    val messageCount: Int = 0,
) {
    val totalTokens: Long
        get() = totalInputTokens + totalOutputTokens + totalCacheReadTokens + totalCacheWriteTokens
}

/**
 * Subscription plans with token limits.
 */
enum class SubscriptionPlan(
    val displayName: String,
    val tokenLimitPerSession: Long,
) {
    PRO("Pro", 500_000L),
    MAX_5("Max (5x)", 2_500_000L),
    MAX_20("Max (20x)", 10_000_000L),
    CUSTOM("Custom", -1L);

    val hasLimit: Boolean get() = tokenLimitPerSession > 0

    companion object {
        fun fromString(value: String): SubscriptionPlan = when (value.lowercase()) {
            "pro" -> PRO
            "max5", "max_5" -> MAX_5
            "max20", "max_20" -> MAX_20
            else -> CUSTOM
        }
    }
}

/**
 * Claude model pricing per million tokens.
 */
enum class ClaudeModel(
    val displayName: String,
    val modelId: String,
    val inputPrice: Double,
    val outputPrice: Double,
    val cacheReadPrice: Double,
    val cacheWritePrice: Double
) {
    OPUS_4("Opus 4", "claude-opus-4", 15.0, 75.0, 1.5, 18.75),
    SONNET_4("Sonnet 4", "claude-sonnet-4", 3.0, 15.0, 0.3, 3.75),
    HAIKU_3_5("Haiku 3.5", "claude-3-5-haiku", 0.80, 4.0, 0.08, 1.0);

    fun calculateCost(record: TokenUsageRecord): Double {
        return (record.inputTokens * inputPrice / 1_000_000.0) +
                (record.outputTokens * outputPrice / 1_000_000.0) +
                (record.cacheReadTokens * cacheReadPrice / 1_000_000.0) +
                (record.cacheWriteTokens * cacheWritePrice / 1_000_000.0)
    }

    companion object {
        fun fromModelId(id: String): ClaudeModel {
            return entries.find { id.contains(it.modelId, ignoreCase = true) } ?: SONNET_4
        }
    }
}

/**
 * Burn rate prediction.
 */
data class BurnRateInfo(
    val tokensPerMinute: Double = 0.0,
    val tokensPerHour: Double = 0.0,
    val costPerHour: Double = 0.0,
    val estimatedTimeToLimitMinutes: Double = Double.POSITIVE_INFINITY,
)

/**
 * Hourly usage data point.
 */
data class HourlyUsage(
    val hour: Int,
    val tokens: Long,
    val cost: Double
)

/**
 * Full dashboard state.
 */
data class DashboardState(
    val isLoading: Boolean = true,
    val isConnected: Boolean = false,
    val lastUpdated: Long = 0,
    val currentSession: UsageSummary = UsageSummary(),
    val todaySummary: UsageSummary = UsageSummary(),
    val last7DaysSummary: UsageSummary = UsageSummary(),
    val burnRate: BurnRateInfo = BurnRateInfo(),
    val plan: SubscriptionPlan = SubscriptionPlan.PRO,
    val usagePercentage: Float = 0f,
    val recentRecords: List<TokenUsageRecord> = emptyList(),
    val hourlyBreakdown: List<HourlyUsage> = emptyList(),
    val errorMessage: String? = null
)

/**
 * App settings shared across platforms.
 */
data class SettingsState(
    val serverHost: String = "192.168.1.100",
    val serverPort: Int = 5123,
    val refreshInterval: Int = 5,
    val planType: String = "pro",
    val darkMode: Boolean = true,
    val notificationsEnabled: Boolean = true,
    val warningThreshold: Int = 70,
    val criticalThreshold: Int = 90,
    val dataMode: DataMode = DataMode.AUTO,
)

/**
 * How the app gets its data.
 */
enum class DataMode {
    /** Read files directly from ~/.claude/ (desktop default) */
    LOCAL,
    /** Connect to companion server via HTTP (android default) */
    REMOTE,
    /** Auto-detect: local on desktop, remote on android */
    AUTO
}

/**
 * Server response models for JSON parsing.
 */
@Serializable
data class UsageResponse(
    val status: String = "",
    val timestamp: Long = 0,
    val plan: String = "",
    val session: SessionData = SessionData(),
    val today: PeriodData = PeriodData(),
    @SerialName("last_7_days") val last7Days: PeriodData = PeriodData(),
    val records: List<RecordData> = emptyList()
)

@Serializable
data class SessionData(
    @SerialName("input_tokens") val inputTokens: Long = 0,
    @SerialName("output_tokens") val outputTokens: Long = 0,
    @SerialName("cache_read_tokens") val cacheReadTokens: Long = 0,
    @SerialName("cache_write_tokens") val cacheWriteTokens: Long = 0,
    @SerialName("total_tokens") val totalTokens: Long = 0,
    @SerialName("total_cost") val totalCost: Double = 0.0,
    @SerialName("message_count") val messageCount: Int = 0,
    @SerialName("session_id") val sessionId: String = "",
)

@Serializable
data class PeriodData(
    @SerialName("input_tokens") val inputTokens: Long = 0,
    @SerialName("output_tokens") val outputTokens: Long = 0,
    @SerialName("cache_read_tokens") val cacheReadTokens: Long = 0,
    @SerialName("cache_write_tokens") val cacheWriteTokens: Long = 0,
    @SerialName("total_tokens") val totalTokens: Long = 0,
    @SerialName("total_cost") val totalCost: Double = 0.0,
    @SerialName("message_count") val messageCount: Int = 0,
    @SerialName("hourly_breakdown") val hourlyBreakdown: List<HourlyData> = emptyList(),
)

@Serializable
data class HourlyData(
    val hour: Int = 0,
    val tokens: Long = 0,
    val cost: Double = 0.0
)

@Serializable
data class RecordData(
    val timestamp: Long = 0,
    @SerialName("input_tokens") val inputTokens: Long = 0,
    @SerialName("output_tokens") val outputTokens: Long = 0,
    @SerialName("cache_read_tokens") val cacheReadTokens: Long = 0,
    @SerialName("cache_write_tokens") val cacheWriteTokens: Long = 0,
    val model: String = "",
    val cost: Double = 0.0
)

@Serializable
data class HealthResponse(
    val status: String = "",
    val version: String = ""
)
