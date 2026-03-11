package com.claudemonitor.tokentracker.data.repository

import com.claudemonitor.tokentracker.data.model.*
import com.claudemonitor.tokentracker.data.network.MonitorApiService
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

class UsageRepository(private val apiService: MonitorApiService) {

    suspend fun checkConnection(): Boolean = withContext(Dispatchers.IO) {
        try {
            val response = apiService.healthCheck()
            response.isSuccessful && response.body()?.status == "ok"
        } catch (e: Exception) {
            false
        }
    }

    suspend fun fetchUsage(hours: Int = 24, plan: String? = null): Result<DashboardState> =
        withContext(Dispatchers.IO) {
            try {
                val response = apiService.getUsageForPeriod(hours, plan)
                if (!response.isSuccessful) {
                    return@withContext Result.failure(
                        Exception("Server error: ${response.code()}")
                    )
                }

                val data = response.body() ?: return@withContext Result.failure(
                    Exception("Empty response")
                )

                val dashboardState = mapToDashboardState(data)
                Result.success(dashboardState)
            } catch (e: Exception) {
                Result.failure(e)
            }
        }

    private fun mapToDashboardState(data: UsageResponse): DashboardState {
        val plan = when (data.plan.lowercase()) {
            "pro" -> SubscriptionPlan.PRO
            "max5", "max_5" -> SubscriptionPlan.MAX_5
            "max20", "max_20" -> SubscriptionPlan.MAX_20
            else -> SubscriptionPlan.CUSTOM
        }

        val currentSession = UsageSummary(
            periodLabel = "Current Session",
            totalInputTokens = data.session.inputTokens,
            totalOutputTokens = data.session.outputTokens,
            totalCacheReadTokens = data.session.cacheReadTokens,
            totalCacheWriteTokens = data.session.cacheWriteTokens,
            totalCost = data.session.totalCost,
            messageCount = data.session.messageCount
        )

        val todaySummary = mapPeriodToSummary("Today", data.today)
        val last7Days = mapPeriodToSummary("Last 7 Days", data.last7Days)

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
        } else {
            0f
        }

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

    private fun mapPeriodToSummary(label: String, period: PeriodData): UsageSummary {
        return UsageSummary(
            periodLabel = label,
            totalInputTokens = period.inputTokens,
            totalOutputTokens = period.outputTokens,
            totalCacheReadTokens = period.cacheReadTokens,
            totalCacheWriteTokens = period.cacheWriteTokens,
            totalCost = period.totalCost,
            messageCount = period.messageCount
        )
    }

    private fun calculateBurnRate(
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
            val remainingTokens = plan.tokenLimitPerSession - totalTokens
            if (remainingTokens > 0) remainingTokens / tokensPerMinute else 0.0
        } else {
            Double.POSITIVE_INFINITY
        }

        val estimatedLimitReachTime = if (estimatedTimeToLimit.isFinite()) {
            System.currentTimeMillis() + (estimatedTimeToLimit * 60_000).toLong()
        } else null

        return BurnRateInfo(
            tokensPerMinute = tokensPerMinute,
            tokensPerHour = tokensPerHour,
            costPerHour = costPerHour,
            estimatedTimeToLimitMinutes = estimatedTimeToLimit,
            estimatedLimitReachTime = estimatedLimitReachTime
        )
    }
}
