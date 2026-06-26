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
import sys
import subprocess

try:
    import requests
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "-q"])
    import requests

try:
    from flask import Flask, jsonify, send_from_directory, request
    from flask_cors import CORS
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "flask", "flask-cors", "-q"])
    from flask import Flask, jsonify, send_from_directory, request
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
        reward_str = r.get("Payout (USDC)", r.get("Reward", r.get("Amount", r.get("Reward (USDC)", ""))))
        title_str = r.get("Quest Name", r.get("Quest Title", r.get("Title", "Unknown")))
        result.append({
            "date": date_str,
            "title": title_str,
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
        reward_val = r.get("Amount (USDC)") or r.get("Amount (USD)") or r.get("Reward USDC") or "0"
        result.append({
            "date": r.get("Date", ""),
            "title": r.get("Source", r.get("Title", "Unknown")),
            "reward": parse_amount(reward_val),
            "platform": platform,
            "status": r.get("Status", "pending")
        })
    return result

def parse_dealwork_payouts() -> list[dict]:
    path = MULTI_EARN / "dealwork-payouts.md"
    result = []
    if not path.exists():
        return result
    content = path.read_text(encoding="utf-8", errors="ignore")
    # Split by blocks starting with ##
    blocks = content.split("## ")
    for block in blocks:
        if not block.strip():
            continue
        # Parse title and ID
        title_match = re.search(r"\[(.*?)\] — Job `(.*?)`", block)
        if not title_match:
            continue
        title = title_match.group(1)
        # Parse time/date
        time_match = re.search(r"-\s+\*\*Time\*\*:\s+([\d\-]+)", block)
        date = time_match.group(1) if time_match else ""
        # Parse expected payout
        payout_match = re.search(r"-\s+\*\*Expected payout\*\*:\s+([\d\.]+)", block)
        reward = float(payout_match.group(1)) if payout_match else 0.0
        # Parse status
        status_match = re.search(r"-\s+\*\*Status\*\*:\s+(.*?)\n", block)
        status = status_match.group(1).strip() if status_match else "pending"
        
        result.append({
            "date": date,
            "title": title,
            "reward": reward,
            "platform": "dealwork",
            "status": status
        })
    return result

def get_all_earnings() -> list[dict]:
    """Combine all platform earnings into one list."""
    all_earnings = []
    all_earnings.extend(get_agenton_submissions())
    all_earnings.extend(get_platform_payouts("BountyBook", "bountybook-payouts.md"))
    all_earnings.extend(get_platform_payouts("Claw Earn", "claw-earn-payouts.md"))
    all_earnings.extend(parse_dealwork_payouts())
    all_earnings.extend(get_platform_payouts("ugig.net", "ugig-payouts.md"))
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
        {"name": "AgentOn_EarnLoop",       "interval": "2h",  "platform": "AgentOn"},
        {"name": "AgentOn_QuestRunner",    "interval": "2h",  "platform": "Quest Runner"},
        {"name": "MultiEarn_BountyBook",   "interval": "3h",  "platform": "BountyBook"},
        {"name": "MultiEarn_ClawEarn",     "interval": "4h",  "platform": "Claw Earn"},
        {"name": "MultiEarn_DealWork",     "interval": "5h",  "platform": "dealwork"},
        {"name": "MultiEarn_Ugig",         "interval": "5h",  "platform": "ugig.net"},
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

# ── Subprocess Management ──────────────────────────────────────────────────────
running_processes = {}

def start_agent_process(platform: str) -> str:
    script_map = {
        "agenton": (ROOT / "scripts" / "earn_loop.py", ROOT / "outputs" / "earn_loop.log"),
        "quest_runner": (ROOT / "agents" / "agenton" / "quest_runner.py", ROOT / "outputs" / "agenton" / "quest_runner.log"),
        "bountybook": (ROOT / "agents" / "multi-earn" / "bountybook_agent.py", ROOT / "outputs" / "multi-earn" / "bountybook.log"),
        "claw": (ROOT / "agents" / "multi-earn" / "claw_earn_agent.py", ROOT / "outputs" / "multi-earn" / "claw.log"),
        "dealwork": (ROOT / "agents" / "multi-earn" / "dealwork_agent.py", ROOT / "outputs" / "multi-earn" / "dealwork.log"),
        "ugig": (ROOT / "agents" / "multi-earn" / "ugig_agent.py", ROOT / "outputs" / "multi-earn" / "ugig.log")
    }
    if platform not in script_map:
        return "invalid_platform"
    
    script_path, log_path = script_map[platform]
    
    p = running_processes.get(platform)
    if p and p.poll() is None:
        return "already_running"
        
    log_path.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        log_file = open(log_path, "w", encoding="utf-8")
        p = subprocess.Popen(
            [sys.executable, str(script_path)],
            cwd=str(ROOT),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True
        )
        running_processes[platform] = p
        return "started"
    except Exception as e:
        return f"error: {str(e)}"

def get_recent_logs(platform: str, num_lines: int = 100) -> str:
    log_map = {
        "agenton": ROOT / "outputs" / "earn_loop.log",
        "quest_runner": ROOT / "outputs" / "agenton" / "quest_runner.log",
        "bountybook": ROOT / "outputs" / "multi-earn" / "bountybook.log",
        "claw": ROOT / "outputs" / "multi-earn" / "claw.log",
        "dealwork": ROOT / "outputs" / "multi-earn" / "dealwork.log",
        "ugig": ROOT / "outputs" / "multi-earn" / "ugig.log"
    }
    if platform not in log_map:
        return "Invalid platform"
    log_path = log_map[platform]
    if not log_path.exists():
        fallback_map = {
            "agenton": ROOT / "outputs" / "submissions-log.md",
            "quest_runner": ROOT / "outputs" / "agenton" / "quest_runner.log",
            "bountybook": ROOT / "outputs" / "multi-earn" / "bountybook-submissions.md",
            "claw": ROOT / "outputs" / "multi-earn" / "claw-earn-submissions.md",
            "dealwork": ROOT / "outputs" / "multi-earn" / "dealwork-submissions.md",
            "ugig": ROOT / "outputs" / "multi-earn" / "ugig-submissions.md"
        }
        fb = fallback_map.get(platform)
        if fb and fb.exists():
            return fb.read_text(encoding="utf-8", errors="ignore")
        return "No logs generated yet. Trigger the loop to see logs."
        
    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
            return "".join(lines[-num_lines:])
    except Exception as e:
        return f"Error reading logs: {str(e)}"

def load_bot_env() -> dict:
    env_path = Path(r"C:\BC RESEARCH\AI_FACTORY\bot.env")
    env = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env

# ── Interaction & Control Routes ──────────────────────────────────────────────

@app.route("/api/run-agent", methods=["POST"])
def api_run_agent():
    data = request.json or {}
    platform = data.get("platform", "")
    status = start_agent_process(platform)
    return jsonify({"status": status})

@app.route("/api/agent-status", methods=["GET"])
def api_agent_status():
    status_dict = {}
    for platform in ["agenton", "quest_runner", "bountybook", "claw", "dealwork", "ugig"]:
        p = running_processes.get(platform)
        status_dict[platform] = "running" if (p and p.poll() is None) else "idle"
    return jsonify(status_dict)

@app.route("/api/logs/<platform>", methods=["GET"])
def api_get_logs(platform):
    lines = int(request.args.get("lines", 100))
    return jsonify({"logs": get_recent_logs(platform, lines)})

@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.json or {}
    user_msg = data.get("message", "")
    
    env = load_bot_env()
    or_key = env.get("OPENROUTER_API_KEY")
    if not or_key:
        return jsonify({
            "response": "Error: OPENROUTER_API_KEY not found in bot.env. Chat assistant is disabled.",
            "action": None
        }), 500

    earnings = get_all_earnings()
    projections = calculate_projections(earnings)
    completed_quests = count_completed_quests()
    twitter_calls = get_twitter_calls_today()
    telegram_actions = get_telegram_actions_today()

    system_prompt = f"""You are the Money Machine Controller, an assistant that manages our autonomous earning agent stack.
You have access to the current system context:
- Total USDC Confirmed: ${projections['total_earned']:.2f}
- Completed Quests: {completed_quests}
- Active Platforms: {projections['platforms_active']}
- Monthly Projected Earnings: ${projections['monthly']:.2f}
- Daily Avg: ${projections['daily_avg']:.2f}
- Today's Twitter API calls: {twitter_calls}/50
- Today's Telegram Actions: {telegram_actions}

Available commands you can trigger:
1. Run AgentOn Loop: triggers `scripts/earn_loop.py`
2. Run AgentOn Quest Runner: triggers `agents/agenton/quest_runner.py`
3. Run BountyBook Loop: triggers `agents/multi-earn/bountybook_agent.py`
4. Run Claw Earn Loop: triggers `agents/multi-earn/claw_earn_agent.py`
5. Run DealWork Loop: triggers `agents/multi-earn/dealwork_agent.py`
6. Run ugig.net Loop: triggers `agents/multi-earn/ugig_agent.py`

If the user wants you to run any of these platforms, set the JSON output fields:
- "action": "run_agent"
- "platform": "agenton" | "quest_runner" | "bountybook" | "claw" | "dealwork" | "ugig"

Respond ONLY in JSON format:
{{
  "response": "Your human-friendly message here",
  "action": "action_name_or_null",
  "platform": "platform_name_or_null"
}}
"""

    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {or_key}", "Content-Type": "application/json"},
            json={
                "model": "google/gemini-2.5-flash",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg}
                ],
                "response_format": {"type": "json_object"},
                "timeout": 30
            }
        )
        if r.status_code == 200:
            resp_data = r.json()["choices"][0]["message"]["content"].strip()
            parsed = json.loads(resp_data)
            
            action = parsed.get("action")
            platform = parsed.get("platform")
            if action == "run_agent" and platform:
                run_status = start_agent_process(platform)
                parsed["response"] += f"\n\n[System: Triggered {platform} loop. Status: {run_status}. Output will stream to the logs panel.]"
            
            return jsonify(parsed)
        else:
            return jsonify({
                "response": f"API Error from OpenRouter (HTTP {r.status_code}): {r.text}",
                "action": None
            }), 500
    except Exception as e:
        return jsonify({
            "response": f"Error calling AI model: {str(e)}",
            "action": None
        }), 500

if __name__ == "__main__":
    print("=" * 60)
    print("  Money Machine Dashboard")
    print("  Open: http://localhost:5050")
    print("=" * 60)
    app.run(host="0.0.0.0", port=5050, debug=True)
