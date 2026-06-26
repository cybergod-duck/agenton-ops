"""
claw_earn_agent.py — Autonomous earn loop for Claw Earn (aiagentstore.ai)

Workflow:
  1. Wallet-signature auth (CLAW_V2 challenge/sign)
  2. Scan open tasks via GET /claw/tasks
  3. Filter by capability + auto-start tier (≤100 USDC for new agent)
  4. Execute with LLM (content, research, analysis)
  5. Submit proof → 48h auto-approve if buyer silent → USDC payout
  6. Log and git push

Run:
    python agents/multi-earn/claw_earn_agent.py

Scheduler: every 4 hours (staggered)
"""

import os
import sys
import json
import time
import subprocess
import requests
from datetime import datetime

# Add multi-earn dir to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from job_scoring import Job, score_job
from prompt_templates import get_template
from payouts_tracker import record_payout, is_category_busy

ROOT_DIR        = r"C:\BC RESEARCH\AI_FACTORY"
AGENTON_DIR     = os.path.join(ROOT_DIR, "AgentOn")
BOT_ENV_PATH    = os.path.join(ROOT_DIR, "bot.env")
CLAW_BASE       = "https://aiagentstore.ai"

PAYOUT_LOG      = os.path.join(AGENTON_DIR, "outputs", "multi-earn", "claw-earn-payouts.md")
SUBMISSIONS_LOG = os.path.join(AGENTON_DIR, "outputs", "multi-earn", "claw-earn-submissions.md")

# New agent limits: only tasks ≤100 USDC until we have 3+ reviews at ≥4.0
MAX_TASK_USDC  = 50.0   # conservative start
MIN_TASK_USDC  = 1.0

# Categories we can handle with LLM alone
SUPPORTED_TYPES = {"content", "research", "analysis", "writing", "data", "summary",
                   "blog", "seo", "email", "news", "report", "document"}

sys.stdout.reconfigure(encoding="utf-8")


def load_env(path: str) -> dict:
    env = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    return env


# ── Wallet Auth (CLAW_V2 challenge) ───────────────────────────────────────────
def get_wallet_address(private_key_hex: str) -> str:
    from eth_account import Account
    return Account.from_key(private_key_hex).address


def claw_sign(private_key_hex: str, challenge: str) -> str:
    """Sign CLAW_V2 challenge message."""
    from eth_account import Account
    from eth_account.messages import encode_defunct
    msg = encode_defunct(text=challenge)
    signed = Account.sign_message(msg, private_key=private_key_hex)
    return signed.signature.hex()


_session_cache: dict = {}


def get_claw_session(address: str, private_key: str) -> str:
    """Return a Claw Earn session token, refreshing if needed."""
    cached = _session_cache.get(address)
    if cached and cached["expires"] > time.time() + 60:
        return cached["token"]

    # Step 1 — get challenge
    r = requests.post(f"{CLAW_BASE}/clawAgentSessionChallenge",
                      json={"address": address}, timeout=15)
    r.raise_for_status()
    challenge = r.json().get("challenge") or r.json().get("nonce", "")
    if not challenge:
        raise RuntimeError(f"No challenge returned: {r.text}")

    # Step 2 — sign (CLAW_V2: prefix if present, else raw)
    if not challenge.startswith("CLAW_V2:"):
        challenge = f"CLAW_V2:{challenge}"
    sig = claw_sign(private_key, challenge)

    # Step 3 — get session
    r2 = requests.post(f"{CLAW_BASE}/clawAgentSession",
                       json={"address": address, "signature": sig}, timeout=15)
    r2.raise_for_status()
    data = r2.json()
    token = data.get("session") or data.get("token") or data.get("sessionId")
    if not token:
        raise RuntimeError(f"No session token returned: {data}")

    exp = time.time() + 3600  # 1h default
    _session_cache[address] = {"token": token, "expires": exp}
    print(f"[CE] Authenticated as {address}")
    return token


# ── Task Fetching ─────────────────────────────────────────────────────────────
def fetch_tasks(session: str) -> list:
    headers = {"Authorization": f"Bearer {session}"}
    r = requests.get(f"{CLAW_BASE}/claw/tasks", headers=headers, timeout=15)
    r.raise_for_status()
    data = r.json()
    return data.get("tasks", data) if isinstance(data, dict) else data


def fetch_task_detail(task_id: str, session: str) -> dict:
    headers = {"Authorization": f"Bearer {session}"}
    r = requests.get(f"{CLAW_BASE}/claw/task",
                     params={"taskId": task_id}, headers=headers, timeout=15)
    r.raise_for_status()
    return r.json()


# ── LLM Execution ─────────────────────────────────────────────────────────────
def execute_task_with_llm(job: Job, keys: dict) -> str | None:
    template = get_template(job.category)
    prompt = template.format(
        title=job.title,
        reward=job.reward_usd,
        description=job.description
    )

    or_key = keys.get("OPENROUTER_API_KEY")
    if not or_key:
        return None

    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {or_key}", "Content-Type": "application/json"},
            json={
                "model": "google/gemini-2.5-flash",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 3000
            },
            timeout=90
        )
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip()
        print(f"[CE] LLM error {r.status_code}: {r.text[:200]}")
        return None
    except Exception as e:
        print(f"[CE] LLM failed: {e}")
        return None


# ── Task Submission ────────────────────────────────────────────────────────────
def submit_task(task_id: str, address: str, deliverable: str, session: str) -> bool:
    """Submit deliverable proof. Returns True on success."""
    import hashlib
    proof_hash = hashlib.sha256(deliverable.encode()).hexdigest()
    headers = {"Authorization": f"Bearer {session}"}

    # Try direct submission endpoint — fallback to generic
    for path in [f"/claw/task/{task_id}/submit", "/claw/submit"]:
        try:
            r = requests.post(
                f"{CLAW_BASE}{path}",
                json={
                    "taskId": task_id,
                    "workerAddress": address,
                    "deliverable": deliverable[:5000],   # truncate if very long
                    "proofHash": proof_hash
                },
                headers=headers,
                timeout=30
            )
            print(f"[CE] Submit {task_id} via {path}: {r.status_code}")
            if r.status_code in (200, 201):
                return True
        except Exception as e:
            print(f"[CE] Submit error on {path}: {e}")

    return False


# ── Logging ───────────────────────────────────────────────────────────────────
def ensure_log(path: str, header: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        with open(path, "w", encoding="utf-8") as f:
            f.write(header)


def log_submission(task_id: str, title: str, reward: float, status: str, notes: str):
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    ensure_log(SUBMISSIONS_LOG,
               "# Claw Earn Submissions Log\n\n| Date | Task ID | Title | Reward USDC | Status | Notes |\n|---|---|---|---|---|---|\n")
    with open(SUBMISSIONS_LOG, "a", encoding="utf-8") as f:
        safe = title.replace("|", "-")[:50]
        f.write(f"| {today} | `{task_id}` | {safe} | {reward:.2f} | {status} | {notes} |\n")


def log_payout(title: str, amount: float, notes: str):
    today = datetime.now().strftime("%Y-%m-%d")
    ensure_log(PAYOUT_LOG,
               "# Claw Earn Payout Tracker\n\n| Date | Source | Amount (USDC) | Status | Notes |\n|---|---|---|---|---|\n")
    with open(PAYOUT_LOG, "a", encoding="utf-8") as f:
        f.write(f"| {today} | {title} | {amount:.2f} | Pending (48h auto) | {notes} |\n")


def sync_git():
    try:
        subprocess.run(["git", "add", "."], check=True, cwd=AGENTON_DIR, capture_output=True)
        res = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, cwd=AGENTON_DIR)
        if res.stdout.strip():
            subprocess.run(["git", "commit", "-m", "bot: claw-earn agent loop update"],
                           check=True, cwd=AGENTON_DIR, capture_output=True)
            subprocess.run(["git", "push"], check=True, cwd=AGENTON_DIR, capture_output=True)
            print("[CE] Git pushed.")
    except Exception as e:
        print(f"[CE] Git sync failed: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"[{datetime.now().isoformat()}] --- Claw Earn Loop Starting ---")
    keys = load_env(BOT_ENV_PATH)

    private_key = keys.get("BOUNTYBOOK_PRIVATE_KEY") or keys.get("AGENT_ETH_PRIVATE_KEY")
    if not private_key:
        print("[CE] ERROR: No AGENT_ETH_PRIVATE_KEY in bot.env")
        print("[CE] This can share the same key as BOUNTYBOOK_PRIVATE_KEY")
        print("[CE] Add: AGENT_ETH_PRIVATE_KEY=0x<your_key>")
        sys.exit(1)

    if not private_key.startswith("0x"):
        private_key = "0x" + private_key

    try:
        address = get_wallet_address(private_key)
        session = get_claw_session(address, private_key)
    except Exception as e:
        print(f"[CE] Auth failed: {e}")
        sys.exit(1)

    # Fetch tasks
    try:
        tasks = fetch_tasks(session)
    except Exception as e:
        print(f"[CE] Failed to fetch tasks: {e}")
        sys.exit(1)

    print(f"[CE] Found {len(tasks)} open tasks")

    submitted = 0
    earned_est = 0.0
    attempted = set()

    for task in tasks:
        task_id  = str(task.get("taskId") or task.get("id") or "")
        title    = task.get("title", "Unknown")
        reward   = float(task.get("reward") or task.get("budget") or 0)
        category = (task.get("category") or task.get("type") or "").lower()

        if not task_id or task_id in attempted:
            continue
        attempted.add(task_id)

        # Filters
        if reward < MIN_TASK_USDC:
            print(f"[CE] Skip {task_id} — reward ${reward:.2f} below min")
            continue
        if reward > MAX_TASK_USDC:
            print(f"[CE] Skip {task_id} — reward ${reward:.2f} above limit")
            continue

        # Check if category/type is supported (fuzzy match)
        is_supported = any(kw in category for kw in SUPPORTED_TYPES)
        if not is_supported and category:
            print(f"[CE] Skip {task_id} — category '{category}' not in supported list")
            continue

        # Get full detail
        try:
            detail = fetch_task_detail(task_id, session)
        except Exception as e:
            print(f"[CE] Detail fetch failed: {e}")
            detail = task

        # ROI Scoring check
        raw_desc = detail.get("requirements", detail.get("description", detail.get("title", "")))
        job_obj = Job(
            id=str(task_id),
            platform="claw earn",
            title=title,
            description=raw_desc,
            reward_usd=reward,
            raw=detail
        )
        try:
            score, evaluated_job = score_job(job_obj, keys.get("OPENROUTER_API_KEY"))
            print(f"[CE] Task {task_id} scored: {score} (Category: {evaluated_job.category}, Complexity: {evaluated_job.complexity}, Ambiguity: {evaluated_job.ambiguity})")
            
            # Check cross-agent category busy lock
            if is_category_busy(evaluated_job.category, str(task_id)):
                print(f"[CE] Skip task {task_id} — category '{evaluated_job.category}' is currently busy with another active job.")
                continue
                
            if score < 0.4:
                print(f"[CE] Skip {task_id} — score {score} is below threshold 0.4")
                log_submission(task_id, title, reward, "skipped", f"Score {score} too low")
                continue
        except Exception as e:
            print(f"[CE] Scoring failed for {task_id}: {e}")
            continue

        print(f"\n[CE] Processing task '{title}' (${reward:.2f}, {category})")

        # Execute with LLM
        deliverable = execute_task_with_llm(evaluated_job, keys)
        if not deliverable:
            print(f"[CE] LLM failed for {task_id} — skip")
            log_submission(task_id, title, reward, "skipped", "LLM failed")
            continue

        # Submit
        ok = submit_task(task_id, address, deliverable, session)
        if ok:
            print(f"[CE] ✅ Submitted task {task_id}")
            log_submission(task_id, title, reward, "submitted", "48h auto-approve pending")
            log_payout(f"Claw Earn: {title[:40]}", reward, f"Task {task_id}")
            
            # Record payout to unified telemetry
            record_payout(
                platform="claw earn",
                job_id=str(task_id),
                title=title,
                category=evaluated_job.category,
                reward_usd=reward,
                status="submitted",
                notes="48h auto-approve pending"
            )
            
            submitted += 1
            earned_est += reward
        else:
            log_submission(task_id, title, reward, "failed", "Submit API error")

        time.sleep(5)

    print(f"\n[CE] Loop complete: {submitted} submitted, ~${earned_est:.2f} USDC pending")
    sync_git()
    print(f"[{datetime.now().isoformat()}] --- Claw Earn Loop Complete ---")


if __name__ == "__main__":
    main()
