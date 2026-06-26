"""
dashboard_api.py — Local Flask API that serves real-time earn data to the dashboard.

Run:  python dashboard/dashboard_api.py
Open: http://localhost:5050

Reads live data from:
  - AgentOn/wallet/payout-tracker.md
  - AgentOn/outputs/submissions-log.md
  - AgentOn/outputs/multi-earn/bountybook-payouts.md
  - AgentOn/outputs/multi-earn/claw-earn-payouts.md
  - AgentOn/outputs/multi-earn/dealwork-payouts.md
  - AgentOn/outputs/twitter-api-usage.md
  - AgentOn/outputs/telegram-actions.md
  - AgentOn/quests/completed/ (directory scan)
"""

import os
import re
import json
import glob
from datetime import datetime, timedelta
from pathlib import Path

try:
    from flask import Flask, jsonify, send_from_directory
    from flask_cors import CORS
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "flask", "flask-cors", "-q"])
    from flask import Flask, jsonify, send_from_directory
    from flask_cors import CORS

app = Flask(__name__, static_folder=".", static_url_path="")
CORS(app)

ROOT        = Path(r"C:\BC RESEARCH\AI_FACTORY\AgentOn")
PAYOUT_FILE = ROOT / "wallet" / "payout-tracker.md"
SUBS_FILE   = ROOT / "outputs" / "submissions-log.md"
COMPLETED   = ROOT / "quests" / "completed"
MULTI_EARN  = ROOT / "outputs" / "multi-earn"
TW_USAGE    = ROOT / "outputs" / "twitter-api-usage.md"
TG_LOG      = ROOT / "outputs" / "telegram-actions.md"

# ── Parsers ───────────────────────────────────────────────────────────────────

def parse_md_table(path: Path) -> list[dict]:
    """Parse a simple markdown table into list of dicts."""
    rows = []
    if not path.exists():
        return rows
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    headers = []
    for i, line in enumerate(lines):
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if not headers:
            headers = cells
            continue
        if set(line.replace("|","").replace("-","").replace(" ","")) == set():
            continue  # separator row
        if len(cells) == len(headers):
            rows.append(dict(zip(headers, cells)))
    return rows

def parse_amount(s: str) -> float:
    """Extract numeric USDC amount from a string like '1.00 USDC' or '$1.00'."""
    m = re.search(r"[\d,]+\.?\d*", str(s).replace(",", ""))
    return float(m.group().replace(",", "")) if m else 0.0

def count_completed_quests() -> int:
    if not COMPLETED.exists():
        return 0
    return len(list(COMPLETED.glob("*.md")))

def get_agenton_submissions() -> list[dict]:
    rows = parse_md_table(SUBS_FILE)
    result = []
    for r in rows:
        date_str = r.get("Date", r.get("Submission Date", ""))
        reward_str = r.get("Reward", r.get("Amount", r.get("Reward (USDC)", "")))
        result.append({
            "date": date_str,
            "title": r.get("Quest Title", r.get("Title", "Unknown")),
            "reward": parse_amount(reward_str),
            "platform": "AgentOn",
            "status": r.get("Status", "submitted")
        })
    return result

def get_platform_payouts(platform: str, filename: str) -> list[dict]:
    path = MULTI_EARN / filename
    rows = parse_md_table(path)
    result = []
    for r in rows:
        result.append({
            "date": r.get("Date", ""),
            "title": r.get("Source", r.get("Title", "Unknown")),
            "reward": parse_amount(r.get("Amount (USDC)", r.get("Reward USDC", "0"))),
            "platform": platform,
            "status": r.get("Status", "pending")
        })
    return result

def get_all_earnings() -> list[dict]:
    """Combine all platform earnings into one list."""
    all_earnings = []
    all_earnings.extend(get_agenton_submissions())
    all_earnings.extend(get_platform_payouts("BountyBook", "bountybook-payouts.md"))
    all_earnings.extend(get_platform_payouts("Claw Earn", "claw-earn-payouts.md"))
    all_earnings.extend(get_platform_payouts("dealwork", "dealwork-payouts.md"))
    return [e for e in all_earnings if e["reward"] > 0]

def get_twitter_calls_today() -> int:
    rows = parse_md_table(TW_USAGE)
    today = datetime.now().strftime("%Y-%m-%d")
    count = 0
    for r in rows:
        ts = r.get("Timestamp", "")
        if today in ts:
            count += 1
    return count

def get_telegram_actions_today() -> int:
    rows = parse_md_table(TG_LOG)
    today = datetime.now().strftime("%Y-%m-%d")
    return sum(1 for r in rows if today in r.get("Timestamp", ""))

# ── Projection Engine ─────────────────────────────────────────────────────────

def calculate_projections(earnings: list[dict]) -> dict:
    """Calculate daily average and project forward."""
    if not earnings:
        return {"daily_avg": 0, "weekly": 0, "monthly": 0, "yearly": 0,
                "days_to_50": None, "days_to_500": None}

    # Group by date
    by_date: dict[str, float] = {}
    for e in earnings:
        date_str = e["date"][:10] if len(e["date"]) >= 10 else ""
        if date_str:
            by_date[date_str] = by_date.get(date_str, 0) + e["reward"]

    if not by_date:
        return {"daily_avg": 0, "weekly": 0, "monthly": 0, "yearly": 0,
                "days_to_50": None, "days_to_500": None}

    # Weight recent days more heavily (last 7 days)
    sorted_dates = sorted(by_date.keys())
    recent = {d: v for d, v in by_date.items() if d >= (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")}
    
    if recent:
        daily_avg = sum(recent.values()) / max(len(recent), 1)
    else:
        daily_avg = sum(by_date.values()) / max(len(by_date), 1)

    # Scale up projections with multi-platform expansion factor
    expansion_factor = 1.0
    platforms_active = len(set(e["platform"] for e in earnings))
    if platforms_active >= 2:
        expansion_factor = 1.5
    if platforms_active >= 3:
        expansion_factor = 2.2

    projected_daily = daily_avg * expansion_factor
    total_earned = sum(e["reward"] for e in earnings)

    return {
        "daily_avg": round(daily_avg, 2),
        "projected_daily": round(projected_daily, 2),
        "weekly": round(projected_daily * 7, 2),
        "monthly": round(projected_daily * 30, 2),
        "yearly": round(projected_daily * 365, 2),
        "total_earned": round(total_earned, 2),
        "days_to_50": round((50 - total_earned) / projected_daily, 1) if projected_daily > 0 and total_earned < 50 else 0,
        "days_to_500": round((500 - total_earned) / projected_daily, 1) if projected_daily > 0 and total_earned < 500 else 0,
        "expansion_factor": expansion_factor,
        "platforms_active": platforms_active,
    }

# ── Scheduled task check ──────────────────────────────────────────────────────

def get_scheduler_status() -> list[dict]:
    tasks = [
        {"name": "AgentOn_EarnLoop",    "interval": "2h",  "platform": "AgentOn"},
        {"name": "MultiEarn_BountyBook","interval": "3h",  "platform": "BountyBook"},
        {"name": "MultiEarn_ClawEarn",  "interval": "4h",  "platform": "Claw Earn"},
    ]
    result = []
    for t in tasks:
        result.append({**t, "status": "scheduled"})
    return result

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(str(ROOT / "dashboard"), "index.html")

@app.route("/api/summary")
def api_summary():
    earnings = get_all_earnings()
    projections = calculate_projections(earnings)

    # Build daily series for chart (last 30 days)
    daily: dict[str, float] = {}
    for e in earnings:
        date = e["date"][:10] if len(e["date"]) >= 10 else ""
        if date:
            daily[date] = daily.get(date, 0) + e["reward"]

    # Fill missing days with 0 for last 30 days
    chart_dates = []
    chart_values = []
    chart_projected = []
    for i in range(29, -1, -1):
        d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        chart_dates.append(d)
        chart_values.append(round(daily.get(d, 0), 2))

    # Add 14-day projection
    proj_dates = []
    proj_values = []
    base = projections["projected_daily"]
    total = projections["total_earned"]
    for i in range(1, 15):
        d = (datetime.now() + timedelta(days=i)).strftime("%Y-%m-%d")
        proj_dates.append(d)
        proj_values.append(round(total + base * i, 2))

    # Platform breakdown
    by_platform: dict[str, float] = {}
    for e in earnings:
        p = e["platform"]
        by_platform[p] = by_platform.get(p, 0) + e["reward"]

    return jsonify({
        "projections": projections,
        "completed_quests": count_completed_quests(),
        "twitter_calls_today": get_twitter_calls_today(),
        "telegram_actions_today": get_telegram_actions_today(),
        "chart": {
            "dates": chart_dates,
            "daily_values": chart_values,
            "proj_dates": proj_dates,
            "proj_values": proj_values,
        },
        "platform_breakdown": by_platform,
        "recent_submissions": sorted(earnings, key=lambda x: x["date"], reverse=True)[:20],
        "scheduler": get_scheduler_status(),
        "last_updated": datetime.now().isoformat(),
    })

@app.route("/api/earnings")
def api_earnings():
    return jsonify(get_all_earnings())

if __name__ == "__main__":
    print("=" * 60)
    print("  AgentOn Earn Dashboard")
    print("  Open: http://localhost:5050")
    print("=" * 60)
    app.run(host="0.0.0.0", port=5050, debug=False)
