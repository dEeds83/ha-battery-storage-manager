package com.claudemonitor.tokentracker.data.network

import com.claudemonitor.tokentracker.data.model.HealthResponse
import com.claudemonitor.tokentracker.data.model.UsageResponse
import retrofit2.Response
import retrofit2.http.GET
import retrofit2.http.Query

/**
 * Retrofit API service for the companion monitor server.
 */
interface MonitorApiService {

    @GET("/health")
    suspend fun healthCheck(): Response<HealthResponse>

    @GET("/usage")
    suspend fun getUsage(
        @Query("hours") hours: Int = 24
    ): Response<UsageResponse>

    @GET("/usage")
    suspend fun getUsageForPeriod(
        @Query("hours") hours: Int,
        @Query("plan") plan: String? = null
    ): Response<UsageResponse>
}
