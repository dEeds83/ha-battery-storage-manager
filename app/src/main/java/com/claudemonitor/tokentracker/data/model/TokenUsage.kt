package com.claudemonitor.tokentracker.data.model

import com.google.gson.annotations.SerializedName

/**
 * Represents a single token usage record from Claude Code.
 */
data class TokenUsageRecord(
    val timestamp: Long = System.currentTimeMillis(),
    @SerializedName("input_tokens") val inputTokens: Long = 0,
    @SerializedName("output_tokens") val outputTokens: Long = 0,
    @SerializedName("cache_read_tokens") val cacheReadTokens: Long = 0,
    @SerializedName("cache_write_tokens") val cacheWriteTokens: Long = 0,
    val model: String = "",
    @SerializedName("session_id") val sessionId: String = "",
    val cost: Double = 0.0
) {
    val totalTokens: Long get() = inputTokens + outputTokens + cacheReadTokens + cacheWriteTokens
}

/**
 * Aggregated usage for a time period.
 */
data class UsageSummary(
    val periodLabel: String = "",
    val totalInputTokens: Long = 0,
    val totalOutputTokens: Long = 0,
    val totalCacheReadTokens: Long = 0,
    val totalCacheWriteTokens: Long = 0,
    val totalCost: Double = 0.0,
    val messageCount: Int = 0,
    val records: List<TokenUsageRecord> = emptyList()
) {
    val totalTokens: Long
        get() = totalInputTokens + totalOutputTokens + totalCacheReadTokens + totalCacheWriteTokens
}

/**
 * Subscription plans with their token limits.
 */
enum class SubscriptionPlan(
    val displayName: String,
    val tokenLimitPerSession: Long,
    val sessionsPerDay: Int
) {
    PRO("Pro", 500_000L, -1),
    MAX_5("Max (5x)", 2_500_000L, -1),
    MAX_20("Max (20x)", 10_000_000L, -1),
    CUSTOM("Custom", -1L, -1);

    val hasLimit: Boolean get() = tokenLimitPerSession > 0
}

/**
 * Model pricing per million tokens.
 */
enum class ClaudeModel(
    val displayName: String,
    val modelId: String,
    val inputPricePerMillion: Double,
    val outputPricePerMillion: Double,
    val cacheReadPricePerMillion: Double,
    val cacheWritePricePerMillion: Double
) {
    OPUS_4("Opus 4", "claude-opus-4", 15.0, 75.0, 1.5, 18.75),
    SONNET_4("Sonnet 4", "claude-sonnet-4", 3.0, 15.0, 0.3, 3.75),
    HAIKU_3_5("Haiku 3.5", "claude-3-5-haiku", 0.80, 4.0, 0.08, 1.0);

    fun calculateCost(record: TokenUsageRecord): Double {
        return (record.inputTokens * inputPricePerMillion / 1_000_000.0) +
                (record.outputTokens * outputPricePerMillion / 1_000_000.0) +
                (record.cacheReadTokens * cacheReadPricePerMillion / 1_000_000.0) +
                (record.cacheWriteTokens * cacheWritePricePerMillion / 1_000_000.0)
    }

    companion object {
        fun fromModelId(id: String): ClaudeModel {
            return entries.find { id.contains(it.modelId, ignoreCase = true) } ?: SONNET_4
        }
    }
}

/**
 * Burn rate prediction data.
 */
data class BurnRateInfo(
    val tokensPerMinute: Double = 0.0,
    val tokensPerHour: Double = 0.0,
    val costPerHour: Double = 0.0,
    val estimatedTimeToLimitMinutes: Double = Double.POSITIVE_INFINITY,
    val estimatedLimitReachTime: Long? = null
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

data class HourlyUsage(
    val hour: Int,
    val tokens: Long,
    val cost: Double
)
