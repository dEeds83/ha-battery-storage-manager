package com.claudemonitor.tokentracker.data.model

import com.google.gson.annotations.SerializedName

/**
 * Response from the companion server's /usage endpoint.
 */
data class UsageResponse(
    val status: String = "",
    val timestamp: Long = 0,
    val plan: String = "",
    val session: SessionData = SessionData(),
    val today: PeriodData = PeriodData(),
    @SerializedName("last_7_days") val last7Days: PeriodData = PeriodData(),
    val records: List<RecordData> = emptyList()
)

data class SessionData(
    @SerializedName("input_tokens") val inputTokens: Long = 0,
    @SerializedName("output_tokens") val outputTokens: Long = 0,
    @SerializedName("cache_read_tokens") val cacheReadTokens: Long = 0,
    @SerializedName("cache_write_tokens") val cacheWriteTokens: Long = 0,
    @SerializedName("total_tokens") val totalTokens: Long = 0,
    @SerializedName("total_cost") val totalCost: Double = 0.0,
    @SerializedName("message_count") val messageCount: Int = 0,
    @SerializedName("session_id") val sessionId: String = ""
)

data class PeriodData(
    @SerializedName("input_tokens") val inputTokens: Long = 0,
    @SerializedName("output_tokens") val outputTokens: Long = 0,
    @SerializedName("cache_read_tokens") val cacheReadTokens: Long = 0,
    @SerializedName("cache_write_tokens") val cacheWriteTokens: Long = 0,
    @SerializedName("total_tokens") val totalTokens: Long = 0,
    @SerializedName("total_cost") val totalCost: Double = 0.0,
    @SerializedName("message_count") val messageCount: Int = 0,
    @SerializedName("hourly_breakdown") val hourlyBreakdown: List<HourlyData> = emptyList()
)

data class HourlyData(
    val hour: Int = 0,
    val tokens: Long = 0,
    val cost: Double = 0.0
)

data class RecordData(
    val timestamp: Long = 0,
    @SerializedName("input_tokens") val inputTokens: Long = 0,
    @SerializedName("output_tokens") val outputTokens: Long = 0,
    @SerializedName("cache_read_tokens") val cacheReadTokens: Long = 0,
    @SerializedName("cache_write_tokens") val cacheWriteTokens: Long = 0,
    val model: String = "",
    val cost: Double = 0.0
)

/**
 * Health check response.
 */
data class HealthResponse(
    val status: String = "",
    val version: String = ""
)
