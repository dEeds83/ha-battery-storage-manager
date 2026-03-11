#!/usr/bin/env python3
"""
Claude Code Token Monitor - Companion Server

Reads Claude Code usage data from ~/.claude/ and serves it as a REST API
for the Android Token Monitor app.

Usage:
    python claude_monitor_server.py [--port 5123] [--host 0.0.0.0]

The server reads JSONL usage logs from Claude Code's local storage and
provides aggregated metrics via HTTP endpoints.
"""

import argparse
import json
import os
import glob
import time
import logging
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Claude Code data paths
CLAUDE_HOME = Path.home() / ".claude"
CLAUDE_PROJECTS = CLAUDE_HOME / "projects"

# Model pricing per million tokens
MODEL_PRICING = {
    "claude-opus-4": {
        "input": 15.0, "output": 75.0,
        "cache_read": 1.5, "cache_write": 18.75
    },
    "claude-sonnet-4": {
        "input": 3.0, "output": 15.0,
        "cache_read": 0.3, "cache_write": 3.75
    },
    "claude-3-5-haiku": {
        "input": 0.80, "output": 4.0,
        "cache_read": 0.08, "cache_write": 1.0
    },
    "claude-sonnet-3.5": {
        "input": 3.0, "output": 15.0,
        "cache_read": 0.3, "cache_write": 3.75
    },
}

# Default to Sonnet pricing
DEFAULT_PRICING = MODEL_PRICING["claude-sonnet-4"]

# Plan limits (tokens per session window)
PLAN_LIMITS = {
    "pro": 500_000,
    "max5": 2_500_000,
    "max20": 10_000_000,
    "custom": -1,
}


def find_usage_files():
    """Find all Claude Code usage/conversation log files."""
    files = []

    # Check for JSONL conversation files in projects directory
    if CLAUDE_PROJECTS.exists():
        for jsonl_file in CLAUDE_PROJECTS.rglob("*.jsonl"):
            files.append(jsonl_file)

    # Check for usage data in main claude directory
    for pattern in ["*.jsonl", "usage*.json", "conversations/*.jsonl"]:
        for f in CLAUDE_HOME.glob(pattern):
            if f.is_file():
                files.append(f)

    return files


def get_model_pricing(model_id: str) -> dict:
    """Get pricing for a model ID."""
    for key, pricing in MODEL_PRICING.items():
        if key in model_id.lower():
            return pricing
    return DEFAULT_PRICING


def calculate_cost(record: dict, pricing: dict) -> float:
    """Calculate the cost of a single usage record."""
    input_tokens = record.get("input_tokens", 0) or 0
    output_tokens = record.get("output_tokens", 0) or 0
    cache_read = record.get("cache_read_tokens", 0) or record.get("cacheReadInputTokens", 0) or 0
    cache_write = record.get("cache_write_tokens", 0) or record.get("cacheCreationInputTokens", 0) or 0

    cost = (
        (input_tokens * pricing["input"] / 1_000_000) +
        (output_tokens * pricing["output"] / 1_000_000) +
        (cache_read * pricing["cache_read"] / 1_000_000) +
        (cache_write * pricing["cache_write"] / 1_000_000)
    )
    return cost


def parse_usage_record(line_data: dict) -> dict | None:
    """Extract token usage from a JSONL line (various Claude Code formats)."""

    # Format 1: Direct usage object
    if "usage" in line_data:
        usage = line_data["usage"]
        timestamp = line_data.get("timestamp", "")
        if isinstance(timestamp, str) and timestamp:
            try:
                ts = int(datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp() * 1000)
            except (ValueError, TypeError):
                ts = int(time.time() * 1000)
        elif isinstance(timestamp, (int, float)):
            ts = int(timestamp) if timestamp > 1e12 else int(timestamp * 1000)
        else:
            ts = int(time.time() * 1000)

        model = line_data.get("model", "")
        input_tokens = usage.get("input_tokens", 0) or 0
        output_tokens = usage.get("output_tokens", 0) or 0
        cache_read = usage.get("cache_read_input_tokens", 0) or usage.get("cacheReadInputTokens", 0) or 0
        cache_write = usage.get("cache_creation_input_tokens", 0) or usage.get("cacheCreationInputTokens", 0) or 0

        if input_tokens == 0 and output_tokens == 0:
            return None

        pricing = get_model_pricing(model)
        record = {
            "timestamp": ts,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read,
            "cache_write_tokens": cache_write,
            "model": model,
        }
        record["cost"] = calculate_cost(record, pricing)
        return record

    # Format 2: costTracker / message with role=assistant
    if line_data.get("role") == "assistant" and "usage" not in line_data:
        # Skip non-usage lines
        return None

    # Format 3: costTracker entries
    if "costTracker" in line_data:
        tracker = line_data["costTracker"]
        records = []
        for model_key, data in tracker.items():
            if isinstance(data, dict):
                record = {
                    "timestamp": int(time.time() * 1000),
                    "input_tokens": data.get("inputTokens", 0) or 0,
                    "output_tokens": data.get("outputTokens", 0) or 0,
                    "cache_read_tokens": data.get("cacheReadInputTokens", 0) or 0,
                    "cache_write_tokens": data.get("cacheCreationInputTokens", 0) or 0,
                    "model": model_key,
                }
                pricing = get_model_pricing(model_key)
                record["cost"] = calculate_cost(record, pricing)
                if record["input_tokens"] > 0 or record["output_tokens"] > 0:
                    records.append(record)
        return records if records else None

    return None


def load_all_records(max_hours: int = 168) -> list[dict]:
    """Load all usage records from Claude Code data files."""
    cutoff_ms = int((time.time() - max_hours * 3600) * 1000)
    all_records = []
    seen_files = set()

    files = find_usage_files()
    logger.info(f"Found {len(files)} potential data files")

    for filepath in files:
        if filepath in seen_files:
            continue
        seen_files.add(filepath)

        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    result = parse_usage_record(data)
                    if result is None:
                        continue

                    if isinstance(result, list):
                        for r in result:
                            if r["timestamp"] >= cutoff_ms:
                                all_records.append(r)
                    else:
                        if result["timestamp"] >= cutoff_ms:
                            all_records.append(result)
        except Exception as e:
            logger.warning(f"Error reading {filepath}: {e}")

    all_records.sort(key=lambda r: r["timestamp"])
    logger.info(f"Loaded {len(all_records)} usage records")
    return all_records


def aggregate_records(records: list[dict]) -> dict:
    """Aggregate records into summary metrics."""
    if not records:
        return {
            "input_tokens": 0, "output_tokens": 0,
            "cache_read_tokens": 0, "cache_write_tokens": 0,
            "total_tokens": 0, "total_cost": 0.0,
            "message_count": 0, "hourly_breakdown": []
        }

    total_input = sum(r["input_tokens"] for r in records)
    total_output = sum(r["output_tokens"] for r in records)
    total_cache_read = sum(r["cache_read_tokens"] for r in records)
    total_cache_write = sum(r["cache_write_tokens"] for r in records)
    total_cost = sum(r["cost"] for r in records)

    # Hourly breakdown
    hourly = defaultdict(lambda: {"tokens": 0, "cost": 0.0})
    for r in records:
        hour = datetime.fromtimestamp(r["timestamp"] / 1000).hour
        total = r["input_tokens"] + r["output_tokens"] + r["cache_read_tokens"] + r["cache_write_tokens"]
        hourly[hour]["tokens"] += total
        hourly[hour]["cost"] += r["cost"]

    hourly_breakdown = [
        {"hour": h, "tokens": data["tokens"], "cost": round(data["cost"], 6)}
        for h, data in sorted(hourly.items())
    ]

    return {
        "input_tokens": total_input,
        "output_tokens": total_output,
        "cache_read_tokens": total_cache_read,
        "cache_write_tokens": total_cache_write,
        "total_tokens": total_input + total_output + total_cache_read + total_cache_write,
        "total_cost": round(total_cost, 6),
        "message_count": len(records),
        "hourly_breakdown": hourly_breakdown
    }


def get_session_records(records: list[dict], session_gap_minutes: int = 30) -> list[dict]:
    """Get records from the most recent session (gap-based detection)."""
    if not records:
        return []

    gap_ms = session_gap_minutes * 60 * 1000
    session_start = len(records) - 1

    for i in range(len(records) - 1, 0, -1):
        if records[i]["timestamp"] - records[i - 1]["timestamp"] > gap_ms:
            break
        session_start = i - 1

    return records[session_start:]


class MonitorHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the monitor API."""

    def log_message(self, format, *args):
        logger.debug(format % args)

    def send_json(self, data: dict, status: int = 200):
        response = json.dumps(data, indent=2)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(response.encode("utf-8"))

    def do_OPTIONS(self):
        self.send_json({})

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/health":
            self.handle_health()
        elif path == "/usage":
            hours = int(params.get("hours", ["24"])[0])
            plan = params.get("plan", ["pro"])[0]
            self.handle_usage(hours, plan)
        else:
            self.send_json({"error": "Not found"}, 404)

    def handle_health(self):
        self.send_json({
            "status": "ok",
            "version": "1.0.0",
            "claude_home": str(CLAUDE_HOME),
            "data_exists": CLAUDE_HOME.exists()
        })

    def handle_usage(self, hours: int, plan: str):
        try:
            all_records = load_all_records(max_hours=hours)

            now = time.time() * 1000
            today_start = int(datetime.now().replace(
                hour=0, minute=0, second=0, microsecond=0
            ).timestamp() * 1000)
            seven_days_ago = int((time.time() - 7 * 86400) * 1000)

            today_records = [r for r in all_records if r["timestamp"] >= today_start]
            week_records = [r for r in all_records if r["timestamp"] >= seven_days_ago]
            session_records = get_session_records(all_records)

            session_agg = aggregate_records(session_records)
            today_agg = aggregate_records(today_records)
            week_agg = aggregate_records(week_records)

            # Recent records (last 50)
            recent = all_records[-50:] if len(all_records) > 50 else all_records

            response = {
                "status": "ok",
                "timestamp": int(time.time() * 1000),
                "plan": plan,
                "session": {
                    **session_agg,
                    "session_id": f"session_{int(time.time())}"
                },
                "today": today_agg,
                "last_7_days": week_agg,
                "records": recent
            }

            self.send_json(response)

        except Exception as e:
            logger.error(f"Error processing usage: {e}", exc_info=True)
            self.send_json({"status": "error", "message": str(e)}, 500)


def main():
    parser = argparse.ArgumentParser(description="Claude Code Token Monitor Server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=5123, help="Bind port (default: 5123)")
    args = parser.parse_args()

    server = HTTPServer((args.host, args.port), MonitorHandler)
    logger.info(f"Claude Token Monitor Server started on {args.host}:{args.port}")
    logger.info(f"Claude home: {CLAUDE_HOME}")
    logger.info(f"Data directory exists: {CLAUDE_HOME.exists()}")

    if CLAUDE_HOME.exists():
        files = find_usage_files()
        logger.info(f"Found {len(files)} data files")
    else:
        logger.warning(f"Claude Code directory not found at {CLAUDE_HOME}")
        logger.warning("Make sure Claude Code is installed and has been used")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Server shutting down...")
        server.server_close()


if __name__ == "__main__":
    main()
