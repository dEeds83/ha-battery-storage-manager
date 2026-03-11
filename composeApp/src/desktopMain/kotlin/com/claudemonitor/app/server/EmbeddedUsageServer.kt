package com.claudemonitor.app.server

import com.claudemonitor.app.data.LocalFileUsageDataSource
import com.claudemonitor.app.data.calculateBurnRate
import com.claudemonitor.app.model.SubscriptionPlan
import com.sun.net.httpserver.HttpExchange
import com.sun.net.httpserver.HttpServer
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import java.net.InetSocketAddress

/**
 * Embedded HTTP server that serves Claude token usage data.
 * Replaces the need for the standalone Python companion server.
 * Android clients can connect directly to this.
 */
class EmbeddedUsageServer(
    private val port: Int = 5123
) {
    private var server: HttpServer? = null
    private val dataSource = LocalFileUsageDataSource()
    private val json = Json { prettyPrint = false; encodeDefaults = true }

    val isRunning: Boolean get() = server != null

    fun start() {
        if (server != null) return

        val httpServer = HttpServer.create(InetSocketAddress("0.0.0.0", port), 0)
        httpServer.createContext("/health") { exchange -> handleHealth(exchange) }
        httpServer.createContext("/usage") { exchange -> handleUsage(exchange) }
        httpServer.executor = null
        httpServer.start()
        server = httpServer
    }

    fun stop() {
        server?.stop(0)
        server = null
    }

    private fun handleHealth(exchange: HttpExchange) {
        val response = """{"status":"ok","version":"2.1.0-embedded"}"""
        sendJson(exchange, 200, response)
    }

    private fun handleUsage(exchange: HttpExchange) {
        try {
            val query = exchange.requestURI.query ?: ""
            val params = parseQuery(query)
            val hours = params["hours"]?.toIntOrNull() ?: 24
            val planStr = params["plan"] ?: "pro"

            val result = runBlocking {
                dataSource.fetchDashboardState(hours, planStr)
            }

            result.fold(
                onSuccess = { state ->
                    val plan = SubscriptionPlan.fromString(planStr)

                    val sessionRecords = state.recentRecords
                    val burnRate = calculateBurnRate(sessionRecords, plan)

                    val responseMap = buildResponseJson(state, planStr)
                    sendJson(exchange, 200, responseMap)
                },
                onFailure = { error ->
                    val errorResponse = """{"status":"error","message":"${error.message?.replace("\"", "\\\"")}"}"""
                    sendJson(exchange, 500, errorResponse)
                }
            )
        } catch (e: Exception) {
            val errorResponse = """{"status":"error","message":"${e.message?.replace("\"", "\\\"")}"}"""
            sendJson(exchange, 500, errorResponse)
        }
    }

    private fun buildResponseJson(
        state: com.claudemonitor.app.model.DashboardState,
        plan: String
    ): String {
        val hourlyJson = state.hourlyBreakdown.joinToString(",") { h ->
            """{"hour":${h.hour},"tokens":${h.tokens},"cost":${h.cost}}"""
        }

        val recordsJson = state.recentRecords.joinToString(",") { r ->
            """{"timestamp":${r.timestamp},"input_tokens":${r.inputTokens},"output_tokens":${r.outputTokens},"cache_read_tokens":${r.cacheReadTokens},"cache_write_tokens":${r.cacheWriteTokens},"model":"${r.model}","cost":${r.cost}}"""
        }

        return """{
"status":"ok",
"timestamp":${System.currentTimeMillis()},
"plan":"$plan",
"session":{
  "input_tokens":${state.currentSession.totalInputTokens},
  "output_tokens":${state.currentSession.totalOutputTokens},
  "cache_read_tokens":${state.currentSession.totalCacheReadTokens},
  "cache_write_tokens":${state.currentSession.totalCacheWriteTokens},
  "total_tokens":${state.currentSession.totalTokens},
  "total_cost":${state.currentSession.totalCost},
  "message_count":${state.currentSession.messageCount},
  "session_id":""
},
"today":{
  "input_tokens":${state.todaySummary.totalInputTokens},
  "output_tokens":${state.todaySummary.totalOutputTokens},
  "cache_read_tokens":${state.todaySummary.totalCacheReadTokens},
  "cache_write_tokens":${state.todaySummary.totalCacheWriteTokens},
  "total_tokens":${state.todaySummary.totalTokens},
  "total_cost":${state.todaySummary.totalCost},
  "message_count":${state.todaySummary.messageCount},
  "hourly_breakdown":[$hourlyJson]
},
"last_7_days":{
  "input_tokens":${state.last7DaysSummary.totalInputTokens},
  "output_tokens":${state.last7DaysSummary.totalOutputTokens},
  "cache_read_tokens":${state.last7DaysSummary.totalCacheReadTokens},
  "cache_write_tokens":${state.last7DaysSummary.totalCacheWriteTokens},
  "total_tokens":${state.last7DaysSummary.totalTokens},
  "total_cost":${state.last7DaysSummary.totalCost},
  "message_count":${state.last7DaysSummary.messageCount},
  "hourly_breakdown":[]
},
"records":[$recordsJson]
}""".replace("\n", "")
    }

    private fun parseQuery(query: String): Map<String, String> {
        if (query.isBlank()) return emptyMap()
        return query.split("&").mapNotNull { param ->
            val parts = param.split("=", limit = 2)
            if (parts.size == 2) parts[0] to parts[1] else null
        }.toMap()
    }

    private fun sendJson(exchange: HttpExchange, statusCode: Int, body: String) {
        val bytes = body.toByteArray(Charsets.UTF_8)
        exchange.responseHeaders.add("Content-Type", "application/json")
        exchange.responseHeaders.add("Access-Control-Allow-Origin", "*")
        exchange.sendResponseHeaders(statusCode, bytes.size.toLong())
        exchange.responseBody.use { it.write(bytes) }
    }
}
