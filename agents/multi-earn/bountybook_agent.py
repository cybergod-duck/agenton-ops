"""
bountybook_agent.py — Autonomous earn loop for BountyBook.ai

Workflow:
  1. Authenticate with ETH private key (EIP-191 sign, no API key needed)
  2. Scan open jobs (research, code, content, data, fetch, transform)
  3. For each automatable job: call LLM to generate deliverable
  4. Submit output → oracle auto-verifies → USDC released to wallet
  5. Log all actions and payouts, git push

Run:
    python agents/multi-earn/bountybook_agent.py

Scheduler: every 3 hours (staggered from AgentOn loop)
"""

import os
import re
import sys
import json
import time
import asyncio
import subprocess
import requests
from datetime import datetime
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
ROOT_DIR       = r"C:\BC RESEARCH\AI_FACTORY"
AGENTON_DIR    = os.path.join(ROOT_DIR, "AgentOn")
BOT_ENV_PATH   = os.path.join(ROOT_DIR, "bot.env")
BB_API_BASE    = "https://api.bountybook.ai"

# Paths
PAYOUT_LOG     = os.path.join(AGENTON_DIR, "outputs", "multi-earn", "bountybook-payouts.md")
SUBMISSIONS_LOG= os.path.join(AGENTON_DIR, "outputs", "multi-earn", "bountybook-submissions.md")
COMPLETED_DIR  = os.path.join(AGENTON_DIR, "outputs", "multi-earn", "completed")

# Categories we can handle with LLM
SUPPORTED_CATEGORIES = {"research", "content", "data", "fetch", "transform", "workflow"}

# Budget guard — skip jobs over this threshold until we build reputation
MAX_JOB_BUDGET_USDC = 20.0
MIN_JOB_BUDGET_USDC = 0.50  # skip dust tasks

sys.stdout.reconfigure(encoding="utf-8")

# ── Env Loading ───────────────────────────────────────────────────────────────
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

# ── ETH Wallet Signing ────────────────────────────────────────────────────────
def eth_sign_message(private_key_hex: str, message: str) -> str:
    """Sign a message using EIP-191 personal_sign (eth_account library)."""
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
        msg = encode_defunct(text=message)
        signed = Account.sign_message(msg, private_key=private_key_hex)
        return signed.signature.hex()
    except ImportError:
        raise RuntimeError("eth_account not installed — run: pip install eth_account")

def get_wallet_address(private_key_hex: str) -> str:
    from eth_account import Account
    return Account.from_key(private_key_hex).address

# ── Auth ──────────────────────────────────────────────────────────────────────
_token_cache: dict = {}

def get_auth_token(address: str, private_key: str) -> str:
    """Get or refresh a BountyBook Bearer token."""
    now = time.time()
    cached = _token_cache.get(address)
    if cached and cached["expires"] > now + 60:
        return cached["token"]

    # Step 1 — get nonce
    r = requests.get(f"{BB_API_BASE}/auth/nonce", params={"address": address}, timeout=15)
    r.raise_for_status()
    nonce = r.json()["nonce"]

    # Step 2 — sign
    sig = eth_sign_message(private_key, nonce)

    # Step 3 — verify
    r2 = requests.post(f"{BB_API_BASE}/auth/verify",
                       json={"address": address, "signature": sig}, timeout=15)
    r2.raise_for_status()
    data = r2.json()
    token = data["token"]
    expires_at = data.get("expiresAt")
    # Parse ISO timestamp or default 1h from now
    try:
        from dateutil.parser import parse as dtparse
        exp = dtparse(expires_at).timestamp() if expires_at else now + 3600
    except Exception:
        exp = now + 3500

    _token_cache[address] = {"token": token, "expires": exp}
    print(f"[BB] Authenticated as {address} (token expires ~{int((exp-now)//60)}m)")
    return token

# ── Job Scanning ──────────────────────────────────────────────────────────────
def fetch_open_jobs(token: str, category: str = None) -> list:
    params = {"status": "open", "limit": 50}
    if category:
        params["category"] = category
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{BB_API_BASE}/jobs", params=params, headers=headers, timeout=15)
    r.raise_for_status()
    data = r.json()
    return data.get("jobs", data) if isinstance(data, dict) else data

def fetch_job_details(job_id: str, token: str) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{BB_API_BASE}/jobs/{job_id}", headers=headers, timeout=15)
    r.raise_for_status()
    return r.json()

# ── LLM Execution ─────────────────────────────────────────────────────────────
def clean_json(text: str) -> str:
    text = text.strip()
    for fence in ("```json", "```"):
        if text.startswith(fence):
            text = text[len(fence):]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()

def execute_job_with_llm(job: dict, keys: dict) -> dict | None:
    """Use LLM to generate a deliverable for a BountyBook job."""
    title = job.get("title", "")
    desc  = job.get("description", "")
    spec  = job.get("spec", {})
    instructions    = spec.get("instructions", desc)
    success_cond    = spec.get("success_condition", "Complete the task accurately.")
    submission_fmt  = job.get("submissionFormat", "text")

    prompt = f"""You are an autonomous AI agent completing a paid bounty task.

Task Title: {title}
Instructions: {instructions}
Success Condition: {success_cond}
Output Format Required: {submission_fmt}

Complete the task and return your output. If the format is "json", return a valid JSON object.
If "text", return plain text. Be accurate, concise, and complete.
Do NOT add preamble or explanation — return the deliverable only."""

    or_key = keys.get("OPENROUTER_API_KEY")
    if not or_key:
        print("[BB] No OPENROUTER_API_KEY — skipping LLM execution")
        return None

    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {or_key}", "Content-Type": "application/json"},
            json={
                "model": "google/gemini-2.5-flash",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 2000
            },
            timeout=60
        )
        if r.status_code == 200:
            content = r.json()["choices"][0]["message"]["content"].strip()
            if submission_fmt == "json":
                try:
                    return {"output": json.loads(clean_json(content))}
                except Exception:
                    return {"output": content}
            return {"output": content}
        else:
            print(f"[BB] LLM API error {r.status_code}: {r.text[:200]}")
            return None
    except Exception as e:
        print(f"[BB] LLM execution failed: {e}")
        return None

# ── Job Claim + Submit ─────────────────────────────────────────────────────────
def claim_job(job_id: str, address: str, token: str) -> bool:
    headers = {"Authorization": f"Bearer {token}"}
    try:
        r = requests.post(f"{BB_API_BASE}/jobs/{job_id}/claim",
                          json={"executorAddress": address},
                          headers=headers, timeout=15)
        if r.status_code == 200:
            print(f"[BB] Claimed job {job_id}")
            return True
        elif r.status_code == 409:
            # Already claimed — try joining queue
            r2 = requests.post(f"{BB_API_BASE}/jobs/{job_id}/queue",
                                headers=headers, timeout=15)
            print(f"[BB] Job {job_id} taken — joined queue: {r2.status_code}")
            return False
        else:
            print(f"[BB] Claim failed {r.status_code}: {r.text[:200]}")
            return False
    except Exception as e:
        print(f"[BB] Claim error: {e}")
        return False

def submit_job(job_id: str, address: str, output_data: dict, token: str) -> dict | None:
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"executorAddress": address, "outputData": output_data}
    try:
        r = requests.post(f"{BB_API_BASE}/jobs/{job_id}/submit",
                          json=payload, headers=headers, timeout=30)
        print(f"[BB] Submit {job_id}: {r.status_code} — {r.text[:200]}")
        if r.status_code == 200:
            return r.json()
        return None
    except Exception as e:
        print(f"[BB] Submit error: {e}")
        return None

# ── Logging ───────────────────────────────────────────────────────────────────
def ensure_log(path: str, header: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        with open(path, "w", encoding="utf-8") as f:
            f.write(header)

def log_submission(job_id: str, title: str, budget: float, status: str, notes: str):
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    ensure_log(SUBMISSIONS_LOG,
               "# BountyBook Submissions Log\n\n| Date | Job ID | Title | Budget USDC | Status | Notes |\n|---|---|---|---|---|---|\n")
    with open(SUBMISSIONS_LOG, "a", encoding="utf-8") as f:
        safe_title = title.replace("|", "-")[:50]
        f.write(f"| {today} | `{job_id}` | {safe_title} | {budget:.2f} | {status} | {notes} |\n")

def log_payout(title: str, amount: float, notes: str):
    today = datetime.now().strftime("%Y-%m-%d")
    ensure_log(PAYOUT_LOG,
               "# BountyBook Payout Tracker\n\n| Date | Source | Amount (USDC) | Status | Notes |\n|---|---|---|---|---|\n")
    with open(PAYOUT_LOG, "a", encoding="utf-8") as f:
        f.write(f"| {today} | {title} | {amount:.2f} | Pending | {notes} |\n")

def sync_git():
    try:
        subprocess.run(["git", "add", "."], check=True, cwd=AGENTON_DIR, capture_output=True)
        res = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, cwd=AGENTON_DIR)
        if res.stdout.strip():
            subprocess.run(["git", "commit", "-m", "bot: bountybook agent loop update"],
                           check=True, cwd=AGENTON_DIR, capture_output=True)
            subprocess.run(["git", "push"], check=True, cwd=AGENTON_DIR, capture_output=True)
            print("[BB] Git pushed.")
    except Exception as e:
        print(f"[BB] Git sync failed: {e}")

# ── Main Loop ─────────────────────────────────────────────────────────────────
def main():
    print(f"[{datetime.now().isoformat()}] --- BountyBook Earn Loop Starting ---")

    keys = load_env(BOT_ENV_PATH)

    # Wallet setup — check for BOUNTYBOOK_PRIVATE_KEY or AGENT_PRIVATE_KEY
    private_key = keys.get("BOUNTYBOOK_PRIVATE_KEY") or keys.get("AGENT_ETH_PRIVATE_KEY")
    if not private_key:
        print("[BB] ERROR: No BOUNTYBOOK_PRIVATE_KEY or AGENT_ETH_PRIVATE_KEY in bot.env")
        print("[BB] Generate one: python -c \"import secrets; print('0x'+secrets.token_hex(32))\"")
        print("[BB] Add to bot.env: BOUNTYBOOK_PRIVATE_KEY=0x<your_key>")
        print("[BB] Fund the address with small ETH on Base (chain 8453) for gas.")
        sys.exit(1)

    if not private_key.startswith("0x"):
        private_key = "0x" + private_key

    try:
        address = get_wallet_address(private_key)
    except Exception as e:
        print(f"[BB] Wallet error: {e}")
        sys.exit(1)

    print(f"[BB] Agent wallet: {address}")

    # Authenticate
    try:
        token = get_auth_token(address, private_key)
    except Exception as e:
        print(f"[BB] Auth failed: {e}")
        sys.exit(1)

    # Check agent stats
    try:
        r = requests.get(f"{BB_API_BASE}/agents/{address}", timeout=10)
        if r.status_code == 200:
            stats = r.json()
            print(f"[BB] Agent stats: tier={stats.get('tier','newcomer')} "
                  f"jobs={stats.get('totalJobs',0)} "
                  f"success={stats.get('successRate',0):.0%} "
                  f"earned={stats.get('totalEarned',0):.2f} USDC")
    except Exception:
        pass

    # Track what we've attempted
    attempted = set()
    submitted_count = 0
    earned_estimate = 0.0

    for category in SUPPORTED_CATEGORIES:
        print(f"\n[BB] Scanning category: {category}")
        try:
            jobs = fetch_open_jobs(token, category=category)
        except Exception as e:
            print(f"[BB] Failed to fetch {category} jobs: {e}")
            continue

        print(f"[BB] Found {len(jobs)} open {category} jobs")

        for job in jobs:
            job_id  = job.get("id") or job.get("job_id")
            title   = job.get("title", "Unknown")
            budget  = float(job.get("budget_usdc") or job.get("budget") or 0)

            if not job_id or job_id in attempted:
                continue
            attempted.add(job_id)

            # Budget filter
            if budget < MIN_JOB_BUDGET_USDC:
                print(f"[BB] Skip {job_id} — budget ${budget:.2f} below minimum")
                continue
            if budget > MAX_JOB_BUDGET_USDC:
                print(f"[BB] Skip {job_id} — budget ${budget:.2f} above safety limit (${MAX_JOB_BUDGET_USDC})")
                continue

            print(f"\n[BB] Processing: '{title}' (${budget:.2f} USDC, {category})")

            # Fetch full details
            try:
                full_job = fetch_job_details(job_id, token)
            except Exception as e:
                print(f"[BB] Failed to fetch details for {job_id}: {e}")
                continue

            # Execute with LLM
            print(f"[BB] Running LLM to generate deliverable...")
            output = execute_job_with_llm(full_job, keys)
            if not output:
                print(f"[BB] LLM failed for {job_id} — skipping")
                log_submission(job_id, title, budget, "skipped", "LLM execution failed")
                continue

            # Claim
            if not claim_job(job_id, address, token):
                log_submission(job_id, title, budget, "queue", "Job taken — joined queue")
                continue

            time.sleep(2)  # brief pause after claim

            # Submit
            result = submit_job(job_id, address, output, token)
            if result:
                print(f"[BB] ✅ Submitted job {job_id} — awaiting oracle verification")
                log_submission(job_id, title, budget, "submitted", f"Oracle pending | budget ${budget:.2f}")
                log_payout(f"BountyBook: {title[:40]}", budget * 0.96, f"Job {job_id} | 4% platform fee")
                submitted_count += 1
                earned_estimate += budget * 0.96
            else:
                log_submission(job_id, title, budget, "failed", "Submit API error")

            time.sleep(5)  # rate limit between submissions

    print(f"\n[BB] Loop complete: {submitted_count} submitted, ~${earned_estimate:.2f} USDC pending")
    sync_git()
    print(f"[{datetime.now().isoformat()}] --- BountyBook Earn Loop Complete ---")

if __name__ == "__main__":
    main()
