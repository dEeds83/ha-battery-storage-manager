# Claude Token Monitor - Cross-Platform App

Real-time monitoring of Claude Code token usage as a **cross-platform app** running natively on **Android** and **macOS/Linux/Windows Desktop**. Built with Kotlin Multiplatform + Compose Multiplatform. Inspired by [Claude-Code-Usage-Monitor](https://github.com/Maciek-roboblog/Claude-Code-Usage-Monitor).

## Features

- **Real-time Dashboard** - Live token usage with auto-refresh (configurable interval)
- **Cross-Platform** - Single codebase for Android + Desktop (macOS, Linux, Windows)
- **Direct File Reading** - Desktop reads `~/.claude/` directly, no server needed
- **Session Tracking** - Current session tokens, cost, and message count
- **Burn Rate Analysis** - Tokens/hour, cost/hour, estimated time to limit
- **Token Breakdown** - Visual split of input/output/cache-read/cache-write tokens
- **Hourly Bar Chart** - Today's usage visualized by hour
- **Plan Support** - Pro, Max 5x, Max 20x, Custom plans with limit tracking
- **Progress Bar with Thresholds** - Color-coded (green/yellow/red) with warning markers
- **7-Day History** - Aggregated weekly usage and cost
- **Dark/Light Theme** - Claude brand-inspired color scheme
- **Configurable Alerts** - Warning and critical threshold notifications
- **Material 3 Design** - Modern Compose UI on all platforms

## Architecture

```
Desktop (macOS/Linux/Windows)           Android
┌──────────────────────┐     ┌─────────────────┐     ┌──────────────────────┐
│  Desktop App         │     │  Android App    │◄───►│  Companion Server    │
│  (Compose Desktop)   │     │  (Compose)      │HTTP │  (Python)            │
│                      │     └─────────────────┘     └──────────┬───────────┘
│  reads directly ─────┤                                        │ reads
│                      │                                        ▼
└──────────┬───────────┘                              ┌──────────────────────┐
           │ reads                                    │  ~/.claude/          │
           ▼                                          │  Claude Code local   │
┌──────────────────────┐                              │  usage data (JSONL)  │
│  ~/.claude/          │                              └──────────────────────┘
│  Claude Code local   │
│  usage data (JSONL)  │
└──────────────────────┘
```

**Desktop:** Reads `~/.claude/` JSONL files directly - no companion server required.
**Android:** Connects to the companion server (Python) over your local network.

## Quick Start

### Desktop (macOS / Linux / Windows)

```bash
# Run the desktop app directly
./gradlew :composeApp:run

# Or build a distributable package
./gradlew :composeApp:packageDmg          # macOS .dmg
./gradlew :composeApp:packageMsi          # Windows .msi
./gradlew :composeApp:packageDeb          # Linux .deb
```

The desktop app reads `~/.claude/` directly. No companion server needed.

### Android

#### 1. Start the Companion Server (on your PC)

```bash
cd companion
python claude_monitor_server.py
# Server starts on 0.0.0.0:5123
```

Options:
```bash
python claude_monitor_server.py --host 0.0.0.0 --port 5123
```

#### 2. Build and Install the Android App

```bash
./gradlew :composeApp:assembleDebug
# APK at: composeApp/build/outputs/apk/debug/composeApp-debug.apk
```

#### 3. Configure

1. Open the app on your Android device
2. Go to **Settings**
3. Enter your PC's local IP address (e.g., `192.168.1.100`)
4. Set port to `5123` (default)
5. Choose your subscription plan
6. Save and return to the dashboard

Both devices must be on the same local network.

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
  "today": { "..." : "..." },
  "last_7_days": { "..." : "..." },
  "records": []
}
```

## Tech Stack

**Cross-Platform App (Kotlin Multiplatform + Compose Multiplatform):**
- Kotlin 2.0 with KMP
- Compose Multiplatform (Material 3)
- kotlinx.serialization for JSON
- kotlinx.coroutines for async
- Custom Canvas charts (no external chart library)
- Platform-specific data sources:
  - Desktop: Direct `java.io.File` reading of `~/.claude/`
  - Android: `java.net.HttpURLConnection` to companion server

**Companion Server (for Android):**
- Python 3.8+ (stdlib only, no pip dependencies)
- Built-in HTTP server

## Project Structure

```
├── composeApp/                        # Cross-platform Compose app
│   ├── build.gradle.kts               # KMP build config
│   └── src/
│       ├── commonMain/kotlin/.../     # Shared code (95%+ of codebase)
│       │   ├── model/Models.kt        # Data classes, enums
│       │   ├── data/UsageDataSource.kt # Interface + shared logic
│       │   └── ui/
│       │       ├── App.kt             # Main app composable
│       │       ├── theme/Theme.kt     # Colors, Material theme
│       │       ├── components/        # StatCard, ProgressBar, Charts
│       │       └── screens/           # Dashboard, Settings
│       ├── androidMain/               # Android-specific
│       │   ├── AndroidManifest.xml
│       │   ├── kotlin/.../
│       │   │   ├── MainActivity.kt
│       │   │   └── data/AndroidNetworkDataSource.kt
│       │   └── res/
│       └── desktopMain/               # Desktop-specific
│           └── kotlin/.../
│               ├── main.kt            # Desktop window entry
│               └── data/
│                   ├── LocalFileUsageDataSource.kt  # Direct file reader
│                   └── NetworkUsageDataSource.kt     # Optional remote mode
├── companion/                         # Python companion server
│   └── claude_monitor_server.py
├── build.gradle.kts                   # Root build
├── settings.gradle.kts
└── gradle/libs.versions.toml          # Version catalog
```

## Requirements

- **Desktop:** JDK 17+, Gradle 8.5+
- **Android:** Android SDK 34, min SDK 26
- **Server:** Python 3.8+
