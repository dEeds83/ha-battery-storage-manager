package com.claudemonitor.app.data

import com.claudemonitor.app.model.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.*
import java.io.File
import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneId

/**
 * Reads Claude Code usage data directly from ~/.claude/ JSONL files.
 * No companion server needed - this runs natively on macOS/Linux/Windows desktop.
 */
class LocalFileUsageDataSource : UsageDataSource {

    private val claudeHome = File(System.getProperty("user.home"), ".claude")
    private val claudeProjects = File(claudeHome, "projects")
    private val json = Json { ignoreUnknownKeys = true }

    override suspend fun checkConnection(): Boolean = withContext(Dispatchers.IO) {
        claudeHome.exists()
    }

    override suspend fun fetchDashboardState(hours: Int, plan: String): Result<DashboardState> =
        withContext(Dispatchers.IO) {
            try {
                val allRecords = loadAllRecords(maxHours = hours)

                val now = System.currentTimeMillis()
                val todayStart = LocalDate.now()
                    .atStartOfDay(ZoneId.systemDefault())
                    .toInstant()
                    .toEpochMilli()
                val sevenDaysAgo = now - 7 * 86400 * 1000L

                val todayRecords = allRecords.filter { it.timestamp >= todayStart }
                val weekRecords = allRecords.filter { it.timestamp >= sevenDaysAgo }
                val sessionRecords = getSessionRecords(allRecords)

                val sessionAgg = aggregateRecords(sessionRecords, "Current Session")
                val todayAgg = aggregateRecords(todayRecords, "Today")
                val weekAgg = aggregateRecords(weekRecords, "Last 7 Days")

                val subscriptionPlan = SubscriptionPlan.fromString(plan)
                val burnRate = calculateBurnRate(
                    allRecords.takeLast(50),
                    subscriptionPlan
                )

                val usagePercentage = if (subscriptionPlan.hasLimit) {
                    (sessionAgg.totalTokens.toFloat() / subscriptionPlan.tokenLimitPerSession.toFloat())
                        .coerceIn(0f, 1f)
                } else 0f

                val hourlyBreakdown = buildHourlyBreakdown(todayRecords)

                Result.success(
                    DashboardState(
                        isLoading = false,
                        isConnected = true,
                        lastUpdated = now,
                        currentSession = sessionAgg,
                        todaySummary = todayAgg,
                        last7DaysSummary = weekAgg,
                        burnRate = burnRate,
                        plan = subscriptionPlan,
                        usagePercentage = usagePercentage,
                        recentRecords = allRecords.takeLast(50),
                        hourlyBreakdown = hourlyBreakdown
                    )
                )
            } catch (e: Exception) {
                Result.failure(e)
            }
        }

    private fun findUsageFiles(): List<File> {
        val files = mutableListOf<File>()

        if (claudeProjects.exists()) {
            claudeProjects.walkTopDown()
                .filter { it.isFile && it.extension == "jsonl" }
                .forEach { files.add(it) }
        }

        if (claudeHome.exists()) {
            claudeHome.listFiles()?.filter {
                it.isFile && (it.extension == "jsonl" ||
                        (it.name.startsWith("usage") && it.extension == "json"))
            }?.forEach { files.add(it) }

            val conversations = File(claudeHome, "conversations")
            if (conversations.exists()) {
                conversations.listFiles()?.filter {
                    it.isFile && it.extension == "jsonl"
                }?.forEach { files.add(it) }
            }
        }

        return files
    }

    private fun loadAllRecords(maxHours: Int): List<TokenUsageRecord> {
        val cutoffMs = System.currentTimeMillis() - maxHours * 3600 * 1000L
        val allRecords = mutableListOf<TokenUsageRecord>()
        val seenFiles = mutableSetOf<String>()

        for (file in findUsageFiles()) {
            val path = file.absolutePath
            if (path in seenFiles) continue
            seenFiles.add(path)

            try {
                file.bufferedReader(Charsets.UTF_8).useLines { lines ->
                    for (line in lines) {
                        val trimmed = line.trim()
                        if (trimmed.isEmpty()) continue

                        val element = try {
                            json.parseToJsonElement(trimmed)
                        } catch (e: Exception) {
                            continue
                        }

                        if (element !is JsonObject) continue

                        val records = parseUsageRecord(element)
                        for (record in records) {
                            if (record.timestamp >= cutoffMs) {
                                allRecords.add(record)
                            }
                        }
                    }
                }
            } catch (_: Exception) {
                // Skip unreadable files
            }
        }

        allRecords.sortBy { it.timestamp }
        return allRecords
    }

    private fun parseUsageRecord(data: JsonObject): List<TokenUsageRecord> {
        val results = mutableListOf<TokenUsageRecord>()

        // Format 1: Direct usage object
        val usage = data["usage"]?.jsonObject
        if (usage != null) {
            val timestamp = parseTimestamp(data["timestamp"])
            val model = data["model"]?.jsonPrimitive?.contentOrNull ?: ""
            val inputTokens = usage["input_tokens"]?.jsonPrimitive?.longOrNull ?: 0L
            val outputTokens = usage["output_tokens"]?.jsonPrimitive?.longOrNull ?: 0L
            val cacheRead = usage["cache_read_input_tokens"]?.jsonPrimitive?.longOrNull
                ?: usage["cacheReadInputTokens"]?.jsonPrimitive?.longOrNull ?: 0L
            val cacheWrite = usage["cache_creation_input_tokens"]?.jsonPrimitive?.longOrNull
                ?: usage["cacheCreationInputTokens"]?.jsonPrimitive?.longOrNull ?: 0L

            if (inputTokens > 0 || outputTokens > 0) {
                val pricing = ClaudeModel.fromModelId(model)
                val record = TokenUsageRecord(
                    timestamp = timestamp,
                    inputTokens = inputTokens,
                    outputTokens = outputTokens,
                    cacheReadTokens = cacheRead,
                    cacheWriteTokens = cacheWrite,
                    model = model,
                )
                results.add(record.copy(cost = pricing.calculateCost(record)))
            }
            return results
        }

        // Format 2: costTracker entries
        val costTracker = data["costTracker"]?.jsonObject
        if (costTracker != null) {
            for ((modelKey, value) in costTracker) {
                if (value !is JsonObject) continue
                val inputTokens = value["inputTokens"]?.jsonPrimitive?.longOrNull ?: 0L
                val outputTokens = value["outputTokens"]?.jsonPrimitive?.longOrNull ?: 0L
                val cacheRead = value["cacheReadInputTokens"]?.jsonPrimitive?.longOrNull ?: 0L
                val cacheWrite = value["cacheCreationInputTokens"]?.jsonPrimitive?.longOrNull ?: 0L

                if (inputTokens > 0 || outputTokens > 0) {
                    val pricing = ClaudeModel.fromModelId(modelKey)
                    val record = TokenUsageRecord(
                        timestamp = System.currentTimeMillis(),
                        inputTokens = inputTokens,
                        outputTokens = outputTokens,
                        cacheReadTokens = cacheRead,
                        cacheWriteTokens = cacheWrite,
                        model = modelKey,
                    )
                    results.add(record.copy(cost = pricing.calculateCost(record)))
                }
            }
        }

        return results
    }

    private fun parseTimestamp(element: JsonElement?): Long {
        if (element == null) return System.currentTimeMillis()
        return when {
            element is JsonPrimitive && element.isString -> {
                try {
                    val str = element.content.replace("Z", "+00:00")
                    Instant.parse(str).toEpochMilli()
                } catch (_: Exception) {
                    System.currentTimeMillis()
                }
            }
            element is JsonPrimitive -> {
                val num = element.doubleOrNull ?: return System.currentTimeMillis()
                if (num > 1e12) num.toLong() else (num * 1000).toLong()
            }
            else -> System.currentTimeMillis()
        }
    }

    private fun getSessionRecords(
        records: List<TokenUsageRecord>,
        gapMinutes: Int = 30
    ): List<TokenUsageRecord> {
        if (records.isEmpty()) return emptyList()

        val gapMs = gapMinutes * 60 * 1000L
        var sessionStart = records.size - 1

        for (i in records.size - 1 downTo 1) {
            if (records[i].timestamp - records[i - 1].timestamp > gapMs) break
            sessionStart = i - 1
        }

        return records.subList(sessionStart, records.size)
    }

    private fun aggregateRecords(
        records: List<TokenUsageRecord>,
        label: String
    ): UsageSummary {
        if (records.isEmpty()) return UsageSummary(periodLabel = label)

        return UsageSummary(
            periodLabel = label,
            totalInputTokens = records.sumOf { it.inputTokens },
            totalOutputTokens = records.sumOf { it.outputTokens },
            totalCacheReadTokens = records.sumOf { it.cacheReadTokens },
            totalCacheWriteTokens = records.sumOf { it.cacheWriteTokens },
            totalCost = records.sumOf { it.cost },
            messageCount = records.size
        )
    }

    private fun buildHourlyBreakdown(records: List<TokenUsageRecord>): List<HourlyUsage> {
        val hourly = mutableMapOf<Int, Pair<Long, Double>>()

        for (record in records) {
            val hour = LocalDateTime.ofInstant(
                Instant.ofEpochMilli(record.timestamp),
                ZoneId.systemDefault()
            ).hour
            val (prevTokens, prevCost) = hourly.getOrDefault(hour, 0L to 0.0)
            hourly[hour] = (prevTokens + record.totalTokens) to (prevCost + record.cost)
        }

        return hourly.entries
            .sortedBy { it.key }
            .map { (hour, data) -> HourlyUsage(hour, data.first, data.second) }
    }
}
