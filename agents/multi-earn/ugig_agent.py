#!/usr/bin/env python3
"""
ugig_agent.py — Autonomous Earn Loop for ugig.net
=================================================
Fetches, scores, claims, and submits deliverables for freelance gigs on ugig.net.
Supports dynamic agent onboarding and a safe Dry-Run mode.
"""

import os
import sys
import time
import re
import json
import secrets
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
import requests

# Add directory to sys.path for importing job_scoring & ugig_client
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from job_scoring import Job, score_job
from ugig_client import UgigClient

# ── Config & Paths ────────────────────────────────────────────────────────────
BOT_ENV_PATH    = Path(r"C:\BC RESEARCH\AI_FACTORY\bot.env")
AGENTON_ROOT    = Path(r"C:\BC RESEARCH\AI_FACTORY\AgentOn")
OUTPUT_DIR      = AGENTON_ROOT / "outputs" / "multi-earn"
SUBMISSIONS_MD  = OUTPUT_DIR / "ugig-submissions.md"
PAYOUTS_MD      = OUTPUT_DIR / "ugig-payouts.md"
LOG_FILE        = OUTPUT_DIR / "ugig.log"

BUDGET_MIN      = 1.0    # USD
BUDGET_MAX      = 100.0  # USD
CAPABILITIES    = ["writing", "research", "code", "data"]
SUBMIT_DELAY    = 5

# Set up logging to console and file
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8")
    ],
)
log = logging.getLogger("ugig_agent")
sys.stdout.reconfigure(encoding="utf-8")

# ── Env Loading ───────────────────────────────────────────────────────────────
def load_env(path: Path = BOT_ENV_PATH) -> dict:
    env = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env

def save_env_key(key: str, value: str, path: Path = BOT_ENV_PATH) -> None:
    content = path.read_text(encoding="utf-8")
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    if pattern.search(content):
        content = pattern.sub(f"{key}={value}", content)
    else:
        if "# ── ugig.net" not in content:
            content += "\n\n# ── ugig.net ──────────────────────────────────────────────\n"
        content += f"{key}={value}\n"
    path.write_text(content, encoding="utf-8")
    log.info(f"bot.env updated: {key}")

# ── Onboarding / Auth Setup ───────────────────────────────────────────────────
def onboard_agent(env: dict) -> UgigClient:
    """Return an authenticated UgigClient, onboarding dynamically if needed."""
    api_key      = env.get("UGIG_API_KEY")
    bearer_token = env.get("UGIG_BEARER_TOKEN")
    email        = env.get("UGIG_EMAIL")
    password     = env.get("UGIG_PASSWORD")
    username     = env.get("UGIG_USERNAME")

    # Scenario 1: Pre-existing API key
    if api_key:
        log.info("Using existing UGIG_API_KEY from bot.env")
        return UgigClient(api_key=api_key)

    # Scenario 2: Pre-existing Bearer Token
    if bearer_token:
        log.info("Using existing UGIG_BEARER_TOKEN from bot.env")
        return UgigClient(bearer_token=bearer_token)

    # Scenario 3: Pre-existing Login credentials
    client = UgigClient()
    if email and password:
        log.info("Found credentials in bot.env, logging in...")
        if client.login(email, password):
            save_env_key("UGIG_BEARER_TOKEN", client.bearer_token)
            return client

    # Scenario 4: Dynamic Onboarding
    log.info("No ugig.net credentials found — starting dynamic onboarding...")
    
    # Generate random credentials
    rand_suffix = secrets.token_hex(4)
    username = f"bcr_agenton_{rand_suffix}"
    # At least 8 chars, 1 uppercase, 1 lowercase, 1 number, 1 special
    password = f"Bcr_AgentOn_{secrets.token_hex(4).capitalize()}1!"
    email = "j0b3@protonmail.com"
    
    agent_name = "BCR-AgentOn-Autonomous"
    description = "Autonomous AI agent specializing in automated software engineering, data processing, copywriting, and market research."

    log.info(f"Attempting signup with username: {username}, email: {email}")
    if client.signup(email, password, username, agent_name, description):
        save_env_key("UGIG_EMAIL", email)
        save_env_key("UGIG_PASSWORD", password)
        save_env_key("UGIG_USERNAME", username)
        
        # Now log in
        if client.login(email, password):
            save_env_key("UGIG_BEARER_TOKEN", client.bearer_token)
            return client
            
    # Try logging in with default email in case already signed up
    log.warning("Signup failed or login pending. Attempting default login check...")
    default_pw = env.get("UGIG_PASSWORD") or "Bcr_AgentOn_default1!"
    try:
        if client.login(email, default_pw):
            save_env_key("UGIG_EMAIL", email)
            save_env_key("UGIG_PASSWORD", default_pw)
            save_env_key("UGIG_BEARER_TOKEN", client.bearer_token)
            return client
    except Exception:
        pass

    print("\n" + "="*80)
    print("  ACTION REQUIRED: ugig.net Account Email Confirmation Needed")
    print(f"  An agent account has been registered with email: {email}")
    print("  Please check your ProtonMail inbox and click the confirmation link,")
    print("  OR log in to ugig.net in your browser, generate an API key at:")
    print("  https://ugig.net/settings/api-keys")
    print("  and save it to bot.env as: UGIG_API_KEY=your_key")
    print("="*80 + "\n")
    raise RuntimeError(f"Email confirmation pending for {email}")

# ── LLM Work Execution ────────────────────────────────────────────────────────
def generate_work_with_llm(job: Job, openrouter_key: str) -> str:
    prompt = f"""You are a professional freelance AI worker completing a contract gig on ugig.net.

Gig Title: {job.title}
Budget: {job.reward_usd} USD
Category: {job.category}
Description:
{job.description}

Complete this task in full and with highest professional quality. 
Return only the completed work/deliverable in clean Markdown format. 
Do not add metadata, introduction, explanation, or conversational preamble.
Provide the actual files, analysis, scripts, or content requested.
"""
    url = f"{LLM_API_BASE}/chat/completions"
    headers = {
        "Authorization": f"Bearer {openrouter_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/BCR-AgentOn",
        "X-Title": "BCR-AgentOn-UgigAgent",
    }
    payload = {
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4000,
        "temperature": 0.5,
    }
    r = requests.post(url, headers=headers, json=payload, timeout=120)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()

# ── Logging Helpers ───────────────────────────────────────────────────────────
def ensure_output_files():
    for f in (SUBMISSIONS_MD, PAYOUTS_MD):
        if not f.exists():
            f.write_text(
                f"# ugig.net {f.stem.replace('-', ' ').title()}\n\n"
                f"_Auto-generated by ugig_agent.py_\n\n---\n\n",
                encoding="utf-8"
            )

def log_submission(job: Job, deliverable: str, status: str = "applied"):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    preview = (deliverable[:300] + "…") if len(deliverable) > 300 else deliverable
    entry = (
        f"## [{job.title}] — Gig `{job.id}`\n"
        f"- **Submitted**: {ts}\n"
        f"- **Reward**: {job.reward_usd} USD\n"
        f"- **Status**: {status}\n"
        f"- **Deliverable preview**:\n\n"
        f"```\n{preview}\n```\n\n---\n\n"
    )
    with SUBMISSIONS_MD.open("a", encoding="utf-8") as fh:
        fh.write(entry)

def log_payout(job: Job, status: str = "Pending approval"):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    entry = (
        f"## [{job.title}] — Gig `{job.id}`\n"
        f"- **Time**: {ts}\n"
        f"- **Status**: {status}\n"
        f"- **Expected payout**: {job.reward_usd} USD\n\n---\n\n"
    )
    with PAYOUTS_MD.open("a", encoding="utf-8") as fh:
        fh.write(entry)

# ── Git Sync ──────────────────────────────────────────────────────────────────
def git_sync(quest_title: str):
    try:
        subprocess.run(["git", "add", "."], cwd=AGENTON_ROOT, check=True, capture_output=True)
        res = subprocess.run(["git", "status", "--porcelain"], cwd=AGENTON_ROOT, capture_output=True, text=True)
        if res.stdout.strip():
            subprocess.run(["git", "commit", "-m", f"bot: ugig gig loop update for {quest_title}"],
                           cwd=AGENTON_ROOT, check=True, capture_output=True)
            subprocess.run(["git", "push"], cwd=AGENTON_ROOT, check=True, capture_output=True)
            log.info("Git push completed.")
    except Exception as e:
        log.warning(f"Git sync failed: {e}")

# ── Main Loop ─────────────────────────────────────────────────────────────────
def main():
    log.info(f"[{datetime.now().isoformat()}] --- Starting ugig.net Earn Loop ---")
    ensure_output_files()
    
    env = load_env()
    openrouter_key = env.get("OPENROUTER_API_KEY")
    if not openrouter_key:
        log.error("OPENROUTER_API_KEY not found in bot.env")
        sys.exit(1)
        
    dry_run = env.get("UGIG_DRY_RUN", "true").lower() == "true"
    if dry_run:
        log.info("👉 DRY-RUN MODE ACTIVE: Gigs will be scored and solved, but not submitted live.")

    # Authenticate / Onboard
    try:
        client = onboard_agent(env)
    except Exception as e:
        log.exception(f"Authentication failed: {e}")
        sys.exit(1)

    # Fetch Gigs
    gigs = client.list_jobs(limit=50)
    log.info(f"Fetched {len(gigs)} unique gigs from ugig.net")

    # Filter & Score gigs
    attempted = set()
    scored_count = 0
    submitted_count = 0
    
    for job in gigs:
        jid = job.id
        status = job.raw.get("status", "active")
        
        # General checks
        if status not in ("active", "draft", ""):
            continue
        if job.reward_usd < BUDGET_MIN:
            continue
        if job.reward_usd > BUDGET_MAX:
            continue
            
        log.info(f"Scoring Gig: '{job.title}' (${job.reward_usd} USD)")
        try:
            score, evaluated_job = score_job(job, openrouter_key)
            log.info(f"Gig {jid} score: {score} (Category: {evaluated_job.category}, Complexity: {evaluated_job.complexity})")
            scored_count += 1
            
            if score < 0.4:
                log.info(f"Skip Gig {jid} — score {score} below 0.4 threshold")
                continue
        except Exception as e:
            log.warning(f"Scoring failed for Gig {jid}: {e}")
            continue

        # Execute task work
        log.info(f"Generating solution for Gig {jid}...")
        try:
            deliverable = generate_work_with_llm(evaluated_job, openrouter_key)
            log.info(f"Solution generated ({len(deliverable)} chars)")
        except Exception as e:
            log.error(f"Failed to generate solution: {e}")
            continue

        if dry_run:
            log.info(f"[DRY-RUN] Would submit deliverable for Gig {jid} with reward ${evaluated_job.reward_usd}")
            log_submission(evaluated_job, deliverable, status="dry-run (simulated)")
            log_payout(evaluated_job, status="dry-run (simulated)")
            
            # Save local copy of deliverable for testing
            mock_dir = OUTPUT_DIR / "ugig" / jid
            mock_dir.mkdir(parents=True, exist_ok=True)
            (mock_dir / "deliverable.md").write_text(deliverable, encoding="utf-8")
            
            submitted_count += 1
            git_sync(evaluated_job.title)
        else:
            # Live claim / application
            log.info(f"Applying / submitting deliverable to Gig {jid}...")
            app_id = client.claim_job(jid, cover_letter=deliverable, proposed_rate=evaluated_job.reward_usd)
            if app_id:
                log_submission(evaluated_job, deliverable, status="submitted")
                log_payout(evaluated_job, status="applied (pending acceptance)")
                submitted_count += 1
                git_sync(evaluated_job.title)
                
                # Stagger submissions
                time.sleep(SUBMIT_DELAY)
            else:
                log_warning(f"Failed to apply to Gig {jid}")

        # Limit concurrent claims per loop to avoid spam
        if submitted_count >= 2:
            log.info("Reached maximum concurrent claims limit (2) for this loop. Staggering rest.")
            break

    log.info(f"ugig.net Earn Loop complete. Scored: {scored_count}, Processed/Submitted: {submitted_count}")
    print(f"[{datetime.now().isoformat()}] --- ugig.net Earn Loop Complete ---")

if __name__ == "__main__":
    main()
