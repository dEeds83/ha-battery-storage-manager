package com.claudemonitor.app.ui.components

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.*
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.claudemonitor.app.model.HourlyUsage
import com.claudemonitor.app.ui.theme.ChartInput
import com.claudemonitor.app.ui.theme.ChartOutput

@Composable
fun HourlyBarChart(
    data: List<HourlyUsage>,
    modifier: Modifier = Modifier
) {
    if (data.isEmpty()) {
        Box(modifier = modifier, contentAlignment = Alignment.Center) {
            Text(
                "No hourly data available",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
        }
        return
    }

    val maxTokens = data.maxOfOrNull { it.tokens } ?: 1L
    val barColor = ChartInput
    val gridColor = MaterialTheme.colorScheme.outline.copy(alpha = 0.3f)
    val labelColor = MaterialTheme.colorScheme.onSurfaceVariant

    Column(modifier = modifier) {
        Canvas(
            modifier = Modifier
                .fillMaxWidth()
                .height(160.dp)
        ) {
            val barCount = data.size
            val barWidth = (size.width / barCount) * 0.7f
            val gap = (size.width / barCount) * 0.3f

            for (i in 0..4) {
                val y = size.height * (1 - i / 4f)
                drawLine(
                    color = gridColor,
                    start = Offset(0f, y),
                    end = Offset(size.width, y),
                    strokeWidth = 0.5f
                )
            }

            data.forEachIndexed { index, usage ->
                val barHeight = if (maxTokens > 0) {
                    (usage.tokens.toFloat() / maxTokens) * size.height * 0.9f
                } else 0f

                val x = index * (barWidth + gap) + gap / 2

                drawRoundRect(
                    color = barColor,
                    topLeft = Offset(x, size.height - barHeight),
                    size = Size(barWidth, barHeight),
                    cornerRadius = CornerRadius(3.dp.toPx(), 3.dp.toPx())
                )
            }
        }

        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween
        ) {
            val labelIndices = when {
                data.size <= 6 -> data.indices.toList()
                data.size <= 12 -> data.indices.filter { it % 2 == 0 }
                else -> data.indices.filter { it % 4 == 0 }
            }
            labelIndices.forEach { idx ->
                if (idx < data.size) {
                    Text(
                        text = "${data[idx].hour}h",
                        style = MaterialTheme.typography.labelSmall,
                        color = labelColor
                    )
                }
            }
        }
    }
}

@Composable
fun TokenBreakdownChart(
    inputTokens: Long,
    outputTokens: Long,
    cacheReadTokens: Long,
    cacheWriteTokens: Long,
    modifier: Modifier = Modifier
) {
    val total = inputTokens + outputTokens + cacheReadTokens + cacheWriteTokens
    if (total == 0L) return

    val segments = listOf(
        ChartSegment("Input", inputTokens, ChartInput),
        ChartSegment("Output", outputTokens, ChartOutput),
        ChartSegment("Cache Read", cacheReadTokens, Color(0xFF8B5CF6)),
        ChartSegment("Cache Write", cacheWriteTokens, Color(0xFF06B6D4))
    ).filter { it.value > 0 }

    Column(modifier = modifier) {
        Canvas(
            modifier = Modifier
                .fillMaxWidth()
                .height(24.dp)
        ) {
            var currentX = 0f
            segments.forEach { segment ->
                val width = (segment.value.toFloat() / total) * size.width
                drawRoundRect(
                    color = segment.color,
                    topLeft = Offset(currentX, 0f),
                    size = Size(width, size.height),
                    cornerRadius = if (currentX == 0f || currentX + width >= size.width) {
                        CornerRadius(6.dp.toPx())
                    } else CornerRadius.Zero
                )
                currentX += width
            }
        }

        Spacer(modifier = Modifier.height(8.dp))

        segments.chunked(2).forEach { row ->
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(16.dp)
            ) {
                row.forEach { segment ->
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        modifier = Modifier.weight(1f)
                    ) {
                        Canvas(modifier = Modifier.size(10.dp)) {
                            drawCircle(color = segment.color)
                        }
                        Spacer(modifier = Modifier.width(4.dp))
                        Text(
                            text = "${segment.label}: ${formatTokenCount(segment.value)}",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                }
                if (row.size == 1) Spacer(modifier = Modifier.weight(1f))
            }
        }
    }
}

private data class ChartSegment(
    val label: String,
    val value: Long,
    val color: Color
)

fun formatTokenCount(count: Long): String {
    return when {
        count >= 1_000_000 -> String.format("%.1fM", count / 1_000_000.0)
        count >= 1_000 -> String.format("%.1fK", count / 1_000.0)
        else -> count.toString()
    }
}

fun formatCost(cost: Double): String {
    return String.format("$%.4f", cost)
}
