package com.claudemonitor.app.ui.components

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.*
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.claudemonitor.app.ui.theme.StatusCritical
import com.claudemonitor.app.ui.theme.StatusOk
import com.claudemonitor.app.ui.theme.StatusWarning

@Composable
fun UsageProgressBar(
    progress: Float,
    label: String,
    currentValue: String,
    maxValue: String,
    warningThreshold: Float = 0.7f,
    criticalThreshold: Float = 0.9f,
    modifier: Modifier = Modifier
) {
    val animatedProgress by animateFloatAsState(
        targetValue = progress.coerceIn(0f, 1f),
        animationSpec = tween(durationMillis = 600),
        label = "progress"
    )

    val progressColor = when {
        animatedProgress >= criticalThreshold -> StatusCritical
        animatedProgress >= warningThreshold -> StatusWarning
        else -> StatusOk
    }

    val trackColor = MaterialTheme.colorScheme.surfaceVariant
    val percentage = (animatedProgress * 100).toInt()

    Column(modifier = modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.Bottom
        ) {
            Text(
                text = label,
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
            Text(
                text = "$percentage%",
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.Bold,
                color = progressColor
            )
        }

        Spacer(modifier = Modifier.height(6.dp))

        Canvas(
            modifier = Modifier
                .fillMaxWidth()
                .height(12.dp)
        ) {
            drawRoundRect(
                color = trackColor,
                cornerRadius = CornerRadius(6.dp.toPx()),
                size = Size(size.width, size.height)
            )

            if (animatedProgress > 0f) {
                drawRoundRect(
                    color = progressColor,
                    cornerRadius = CornerRadius(6.dp.toPx()),
                    size = Size(size.width * animatedProgress, size.height)
                )
            }

            val warningX = size.width * warningThreshold
            drawLine(
                color = Color.White.copy(alpha = 0.5f),
                start = Offset(warningX, 0f),
                end = Offset(warningX, size.height),
                strokeWidth = 1.5f
            )

            val criticalX = size.width * criticalThreshold
            drawLine(
                color = Color.White.copy(alpha = 0.5f),
                start = Offset(criticalX, 0f),
                end = Offset(criticalX, size.height),
                strokeWidth = 1.5f
            )
        }

        Spacer(modifier = Modifier.height(4.dp))

        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween
        ) {
            Text(
                text = currentValue,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
            Text(
                text = maxValue,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
        }
    }
}
