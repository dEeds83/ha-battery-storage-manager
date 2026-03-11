# Claude Token Monitor - Android App

Real-time monitoring of Claude Code token usage as a native Android app. Inspired by [Claude-Code-Usage-Monitor](https://github.com/Maciek-roboblog/Claude-Code-Usage-Monitor).

## Features

- **Real-time Dashboard** - Live token usage with auto-refresh (configurable interval)
- **Session Tracking** - Current session tokens, cost, and message count
- **Burn Rate Analysis** - Tokens/hour, cost/hour, estimated time to limit
- **Token Breakdown** - Visual split of input/output/cache-read/cache-write tokens
- **Hourly Bar Chart** - Today's usage visualized by hour
- **Plan Support** - Pro, Max 5x, Max 20x, Custom plans with limit tracking
- **Progress Bar with Thresholds** - Color-coded (green/yellow/red) with warning markers
- **7-Day History** - Aggregated weekly usage and cost
- **Dark/Light Theme** - Claude brand-inspired color scheme
- **Configurable Alerts** - Warning (70%) and critical (90%) threshold notifications
- **Material 3 Design** - Modern Jetpack Compose UI

## Architecture

```
┌─────────────────┐     HTTP/JSON      ┌──────────────────────┐
│  Android App    │◄──────────────────►│  Companion Server    │
│  (Kotlin/       │    Port 5123       │  (Python)            │
│   Compose)      │                    │                      │
└─────────────────┘                    └──────────┬───────────┘
                                                  │
                                                  │ reads
                                                  ▼
                                       ┌──────────────────────┐
                                       │  ~/.claude/          │
                                       │  Claude Code local   │
                                       │  usage data (JSONL)  │
                                       └──────────────────────┘
```

## Quick Start

### 1. Start the Companion Server (on your PC)

```bash
cd companion
python claude_monitor_server.py
# Server starts on 0.0.0.0:5123
```

Options:
```bash
python claude_monitor_server.py --host 0.0.0.0 --port 5123
```

### 2. Configure the Android App

1. Open the app on your Android device
2. Go to **Settings**
3. Enter your PC's local IP address (e.g., `192.168.1.100`)
4. Set port to `5123` (default)
5. Choose your subscription plan
6. Save and return to the dashboard

Both devices must be on the same local network.

### 3. Build the Android App

```bash
./gradlew assembleDebug
# APK at: app/build/outputs/apk/debug/app-debug.apk
```

## API Endpoints

The companion server exposes:

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Server health check |
| `GET /usage?hours=24&plan=pro` | Token usage data |

### Example Response

```json
{
  "status": "ok",
  "timestamp": 1710000000000,
  "plan": "pro",
  "session": {
    "input_tokens": 125000,
    "output_tokens": 45000,
    "cache_read_tokens": 80000,
    "cache_write_tokens": 12000,
    "total_tokens": 262000,
    "total_cost": 1.2345,
    "message_count": 42
  },
  "today": { ... },
  "last_7_days": { ... },
  "records": [ ... ]
}
```

## Tech Stack

**Android App:**
- Kotlin
- Jetpack Compose + Material 3
- Retrofit + OkHttp
- DataStore Preferences
- Custom Canvas charts (no external chart library)

**Companion Server:**
- Python 3.8+ (stdlib only, no pip dependencies)
- Built-in HTTP server

## Project Structure

```
├── app/                          # Android application
│   └── src/main/
│       ├── java/.../tokentracker/
│       │   ├── data/
│       │   │   ├── model/        # Data classes
│       │   │   ├── network/      # Retrofit API
│       │   │   └── repository/   # Data layer
│       │   ├── service/          # Background services
│       │   ├── ui/
│       │   │   ├── components/   # Reusable Compose widgets
│       │   │   ├── screens/      # Dashboard, Settings
│       │   │   └── theme/        # Colors, typography
│       │   └── util/
│       └── res/
├── companion/                    # PC-side server
│   └── claude_monitor_server.py
├── build.gradle.kts
└── README.md
```
