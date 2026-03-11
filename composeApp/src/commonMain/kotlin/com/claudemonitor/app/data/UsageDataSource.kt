package com.claudemonitor.app.data

import com.claudemonitor.app.model.*

/**
 * Platform-agnostic interface for fetching Claude token usage data.
 * - On Desktop: reads ~/.claude/ files directly
 * - On Android: connects to the companion server via HTTP
 */
interface UsageDataSource {
    suspend fun fetchDashboardState(hours: Int, plan: String): Result<DashboardState>
    suspend fun checkConnection(): Boolean
}

/**
 * Shared logic for calculating burn rate from recent records.
 */
fun calculateBurnRate(
    records: List<TokenUsageRecord>,
    plan: SubscriptionPlan
): BurnRateInfo {
    if (records.size < 2) return BurnRateInfo()

    val sorted = records.sortedBy { it.timestamp }
    val timeSpanMinutes = (sorted.last().timestamp - sorted.first().timestamp) / 60_000.0
    if (timeSpanMinutes <= 0) return BurnRateInfo()

    val totalTokens = sorted.sumOf { it.totalTokens }
    val totalCost = sorted.sumOf { it.cost }

    val tokensPerMinute = totalTokens / timeSpanMinutes
    val tokensPerHour = tokensPerMinute * 60
    val costPerHour = (totalCost / timeSpanMinutes) * 60

    val estimatedTimeToLimit = if (plan.hasLimit && tokensPerMinute > 0) {
        val remaining = plan.tokenLimitPerSession - totalTokens
        if (remaining > 0) remaining / tokensPerMinute else 0.0
    } else {
        Double.POSITIVE_INFINITY
    }

    return BurnRateInfo(
        tokensPerMinute = tokensPerMinute,
        tokensPerHour = tokensPerHour,
        costPerHour = costPerHour,
        estimatedTimeToLimitMinutes = estimatedTimeToLimit,
    )
}

/**
 * Maps server response to DashboardState (used by both network and local sources).
 */
fun mapResponseToDashboardState(data: UsageResponse): DashboardState {
    val plan = SubscriptionPlan.fromString(data.plan)

    val currentSession = UsageSummary(
        periodLabel = "Current Session",
        totalInputTokens = data.session.inputTokens,
        totalOutputTokens = data.session.outputTokens,
        totalCacheReadTokens = data.session.cacheReadTokens,
        totalCacheWriteTokens = data.session.cacheWriteTokens,
        totalCost = data.session.totalCost,
        messageCount = data.session.messageCount
    )

    val todaySummary = UsageSummary(
        periodLabel = "Today",
        totalInputTokens = data.today.inputTokens,
        totalOutputTokens = data.today.outputTokens,
        totalCacheReadTokens = data.today.cacheReadTokens,
        totalCacheWriteTokens = data.today.cacheWriteTokens,
        totalCost = data.today.totalCost,
        messageCount = data.today.messageCount
    )

    val last7Days = UsageSummary(
        periodLabel = "Last 7 Days",
        totalInputTokens = data.last7Days.inputTokens,
        totalOutputTokens = data.last7Days.outputTokens,
        totalCacheReadTokens = data.last7Days.cacheReadTokens,
        totalCacheWriteTokens = data.last7Days.cacheWriteTokens,
        totalCost = data.last7Days.totalCost,
        messageCount = data.last7Days.messageCount
    )

    val recentRecords = data.records.map { r ->
        TokenUsageRecord(
            timestamp = r.timestamp,
            inputTokens = r.inputTokens,
            outputTokens = r.outputTokens,
            cacheReadTokens = r.cacheReadTokens,
            cacheWriteTokens = r.cacheWriteTokens,
            model = r.model,
            cost = r.cost
        )
    }

    val hourlyBreakdown = data.today.hourlyBreakdown.map { h ->
        HourlyUsage(hour = h.hour, tokens = h.tokens, cost = h.cost)
    }

    val burnRate = calculateBurnRate(recentRecords, plan)

    val usagePercentage = if (plan.hasLimit) {
        (currentSession.totalTokens.toFloat() / plan.tokenLimitPerSession.toFloat())
            .coerceIn(0f, 1f)
    } else 0f

    return DashboardState(
        isLoading = false,
        isConnected = true,
        lastUpdated = data.timestamp,
        currentSession = currentSession,
        todaySummary = todaySummary,
        last7DaysSummary = last7Days,
        burnRate = burnRate,
        plan = plan,
        usagePercentage = usagePercentage,
        recentRecords = recentRecords,
        hourlyBreakdown = hourlyBreakdown
    )
}
