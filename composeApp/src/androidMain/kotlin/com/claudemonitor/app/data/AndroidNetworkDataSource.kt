package com.claudemonitor.app.data

import com.claudemonitor.app.model.DashboardState
import com.claudemonitor.app.model.HealthResponse
import com.claudemonitor.app.model.UsageResponse
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import java.net.HttpURLConnection
import java.net.URL

/**
 * Android implementation: fetches usage data from the companion server via HTTP.
 */
class AndroidNetworkDataSource(
    private val host: String = "192.168.1.100",
    private val port: Int = 5123,
) : UsageDataSource {

    private val json = Json { ignoreUnknownKeys = true }
    private val baseUrl get() = "http://$host:$port"

    override suspend fun checkConnection(): Boolean = withContext(Dispatchers.IO) {
        try {
            val url = URL("$baseUrl/health")
            val connection = url.openConnection() as HttpURLConnection
            connection.connectTimeout = 5000
            connection.readTimeout = 5000
            connection.requestMethod = "GET"

            val responseCode = connection.responseCode
            if (responseCode == 200) {
                val body = connection.inputStream.bufferedReader().readText()
                val health = json.decodeFromString<HealthResponse>(body)
                health.status == "ok"
            } else false
        } catch (_: Exception) {
            false
        }
    }

    override suspend fun fetchDashboardState(hours: Int, plan: String): Result<DashboardState> =
        withContext(Dispatchers.IO) {
            try {
                val url = URL("$baseUrl/usage?hours=$hours&plan=$plan")
                val connection = url.openConnection() as HttpURLConnection
                connection.connectTimeout = 10000
                connection.readTimeout = 10000
                connection.requestMethod = "GET"

                val responseCode = connection.responseCode
                if (responseCode != 200) {
                    return@withContext Result.failure(
                        Exception("Server returned $responseCode")
                    )
                }

                val body = connection.inputStream.bufferedReader().readText()
                val response = json.decodeFromString<UsageResponse>(body)
                Result.success(mapResponseToDashboardState(response))
            } catch (e: Exception) {
                Result.failure(e)
            }
        }
}
