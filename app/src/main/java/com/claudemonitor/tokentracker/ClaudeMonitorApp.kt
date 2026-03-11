package com.claudemonitor.tokentracker

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.os.Build

class ClaudeMonitorApp : Application() {

    companion object {
        const val NOTIFICATION_CHANNEL_ID = "claude_usage_alerts"
    }

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                NOTIFICATION_CHANNEL_ID,
                "Usage Alerts",
                NotificationManager.IMPORTANCE_HIGH
            ).apply {
                description = "Alerts when Claude token usage approaches limits"
            }

            val notificationManager = getSystemService(NotificationManager::class.java)
            notificationManager.createNotificationChannel(channel)
        }
    }
}
