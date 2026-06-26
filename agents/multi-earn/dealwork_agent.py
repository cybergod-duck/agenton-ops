#!/usr/bin/env python3
"""
dealwork_agent.py — Autonomous Earn Loop for dealwork.ai
=========================================================
Platform : https://dealwork.ai
Auth     : HMAC-SHA256 (X-Agent-ID + X-Signature + X-Timestamp) + Bearer JWT
Payment  : USDC on Base (x402), 3% AI-to-AI fee
LLM      : OpenRouter → google/gemini-2.5-flash

Loop:
  1. Onboard once (cache DEALWORK_AGENT_ID + DEALWORK_AGENT_SECRET to bot.env)
  2. Scan matching jobs (budget 1.0–50.0 USDC, skip attempted)
  3. Claim → execute with LLM → deliver via PATCH
  4. Log to outputs/multi-earn/dealwork-submissions.md & dealwork-payouts.md
  5. Git add/commit/push from AgentOn root after each loop
"""

import sys
sys.stdout.reconfigure(encoding="utf-8")

import os
import re
import time
import hmac
import json
import hashlib
import logging
import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import requests

# Add multi-earn dir to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from job_scoring import Job, score_job, execute_job_with_llm
from prompt_templates import get_template
from payouts_tracker import record_payout, is_category_busy

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────
BOT_ENV_PATH   = Path(r"C:\BC RESEARCH\AI_FACTORY\bot.env")
AGENTON_ROOT   = Path(r"C:\BC RESEARCH\AI_FACTORY\AgentOn")
OUTPUT_DIR     = AGENTON_ROOT / "outputs" / "multi-earn"
SUBMISSIONS_MD = OUTPUT_DIR / "dealwork-submissions.md"
PAYOUTS_MD     = OUTPUT_DIR / "dealwork-payouts.md"

API_BASE       = "https://dealwork.ai/api/v1"
LLM_MODEL      = "google/gemini-2.5-flash"
LLM_API_BASE   = "https://openrouter.ai/api/v1"
CAPABILITIES   = ["writing", "research", "code", "data"]

BUDGET_MIN     = 1.0    # USDC
BUDGET_MAX     = 50.0   # USDC
SUBMIT_DELAY   = 5      # seconds between submissions
LOOP_DELAY     = 60     # seconds between full scan loops
MAX_LOOPS      = 0      # 0 = run forever

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("dealwork")


# ─────────────────────────────────────────────────────────────────────────────
# env helpers
# ─────────────────────────────────────────────────────────────────────────────
def load_env(path: Path = BOT_ENV_PATH) -> dict:
    """Parse KEY=VALUE lines from bot.env; ignores comments and blank lines."""
    env: dict = {}
    if not path.exists():
        raise FileNotFoundError(f"bot.env not found: {path}")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


def save_env_key(key: str, value: str, path: Path = BOT_ENV_PATH) -> None:
    """
    Upsert a KEY=VALUE line in bot.env.
    - If the key exists, replace the value in-place.
    - If not, append to the DealWork section (or end of file).
    """
    content = path.read_text(encoding="utf-8")
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    if pattern.search(content):
        content = pattern.sub(f"{key}={value}", content)
    else:
        # Append under a DealWork section header if not present
        if "# ── DealWork" not in content:
            content += "\n\n# ── DealWork ──────────────────────────────────────────────\n"
        content += f"{key}={value}\n"
    path.write_text(content, encoding="utf-8")
    log.info("bot.env updated: %s", key)


# ─────────────────────────────────────────────────────────────────────────────
# HMAC Auth
# ─────────────────────────────────────────────────────────────────────────────
def hmac_headers(agent_id: str, agent_secret: str, bearer_token: str | None = None) -> dict:
    """
    Build signed request headers for dealwork.ai:
      X-Agent-ID  : registered agent UUID
      X-Timestamp : Unix timestamp (seconds, string)
      X-Signature : HMAC-SHA256(timestamp, agent_secret)
    Optionally includes Authorization: Bearer <jwt>
    """
    timestamp = str(int(time.time()))
    sig = hmac.new(
        agent_secret.encode("utf-8"),
        timestamp.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-Agent-ID": agent_id,
        "X-Timestamp": timestamp,
        "X-Signature": sig,
    }
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    return headers


# ─────────────────────────────────────────────────────────────────────────────
# API wrappers
# ─────────────────────────────────────────────────────────────────────────────
class DealworkClient:
    """Thin wrapper around the dealwork.ai REST API."""

    def __init__(self, agent_id: str, agent_secret: str, bearer_token: str | None = None):
        self.agent_id     = agent_id
        self.agent_secret = agent_secret
        self.bearer_token = bearer_token
        self.session      = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    def _headers(self) -> dict:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        else:
            headers.update(hmac_headers(self.agent_id, self.agent_secret))
        return headers

    def get(self, path: str, params: dict | None = None, timeout: int = 30) -> dict | list:
        url = f"{API_BASE}{path}"
        r = self.session.get(url, headers=self._headers(), params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, payload: dict | None = None, timeout: int = 30) -> dict:
        url = f"{API_BASE}{path}"
        r = self.session.post(url, headers=self._headers(), json=payload or {}, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def patch(self, path: str, payload: dict, timeout: int = 30) -> dict:
        url = f"{API_BASE}{path}"
        r = self.session.patch(url, headers=self._headers(), json=payload, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def post_chat(self, job_id: str, message: str) -> dict:
        return self.post(f"/jobs/{job_id}/chat", {"message": message})


# ─────────────────────────────────────────────────────────────────────────────
# Registration / Onboarding
# ─────────────────────────────────────────────────────────────────────────────
def onboard_agent(env: dict) -> tuple[str, str, str | None]:
    """
    Return (agent_id, agent_secret, bearer_token).
    - If DEALWORK_AGENT_ID + DEALWORK_AGENT_SECRET already in env → verify via GET /agents/me
    - Otherwise → POST /agents/onboard, cache results to bot.env
    """
    agent_id     = env.get("DEALWORK_AGENT_ID", "")
    agent_secret = env.get("DEALWORK_AGENT_SECRET", "")
    bearer_token = env.get("DEALWORK_BEARER_TOKEN", "") or None

    if agent_id and agent_secret:
        log.info("Existing DealWork credentials found — verifying via GET /agents/me …")
        client = DealworkClient(agent_id, agent_secret, bearer_token)
        try:
            profile_resp = client.get("/agents/me")
            profile = profile_resp.get("data") if isinstance(profile_resp, dict) else {}
            log.info("Verified: agent=%s healthy=%s", profile.get("accountId") or profile.get("id"), profile.get("isHealthy"))
            # Refresh bearer token if returned
            fresh_token = profile_resp.get("token") or profile.get("token") or bearer_token
            if fresh_token and fresh_token != bearer_token:
                save_env_key("DEALWORK_BEARER_TOKEN", fresh_token)
            return agent_id, agent_secret, fresh_token or bearer_token
        except requests.HTTPError as exc:
            sc = exc.response.status_code
            if sc == 401:
                claim_url = env.get("DEALWORK_CLAIM_URL", "unknown")
                log.warning("GET /agents/me returned 401 (Unauthorized). The agent is likely unclaimed.")
                log.warning("👉 Please visit the Claim URL: %s", claim_url)
                return agent_id, agent_secret, bearer_token
            else:
                log.warning("GET /agents/me failed (%s) — will re-onboard.", sc)

    # ── Fresh registration ────────────────────────────────────────────────────
    log.info("Onboarding new agent to dealwork.ai …")

    # Generate a fresh secret for this agent
    new_secret = secrets.token_hex(32)

    # Use a temporary throwaway ID for the onboard call (the server will issue a real one)
    tmp_id = f"bcr-agenton-{secrets.token_hex(6)}"

    payload = {
        "agentName":    "BCR-AgentOn",
        "description":  "Autonomous AI worker specializing in professional writing, market research, coding tasks, and data analysis.",
        "capabilities": CAPABILITIES,
        "capabilityTags": CAPABILITIES,
        "agent_id":     tmp_id,
        "secret":       new_secret,
        "autonomous":   True,
        "ownerEmail":   "j0b3@protonmail.com",
    }

    # For onboarding we don't yet have a valid HMAC pair → use tmp credentials
    tmp_client = DealworkClient(tmp_id, new_secret)
    try:
        resp = tmp_client.post("/agents/onboard", payload)
    except requests.HTTPError as exc:
        raise RuntimeError(f"Onboard failed: {exc.response.status_code} {exc.response.text}") from exc

    data = resp.get("data") or {}
    real_id     = data.get("agentAccountId") or resp.get("agent_id") or resp.get("id") or tmp_id
    real_secret = data.get("hmacSecret") or resp.get("secret") or new_secret
    real_token  = data.get("apiKey") or data.get("token") or resp.get("token") or resp.get("bearer_token") or None
    claim_url   = data.get("claimUrl") or resp.get("claimUrl") or None

    log.info("Onboarded! agent_id=%s", real_id)
    if claim_url:
        log.info("CLAIM URL (Action Required): %s", claim_url)
        save_env_key("DEALWORK_CLAIM_URL", claim_url)

    save_env_key("DEALWORK_AGENT_ID",     real_id)
    save_env_key("DEALWORK_AGENT_SECRET", real_secret)
    if real_token:
        save_env_key("DEALWORK_BEARER_TOKEN", real_token)

    return real_id, real_secret, real_token


# ─────────────────────────────────────────────────────────────────────────────
# LLM execution
# ─────────────────────────────────────────────────────────────────────────────
def llm_generate(prompt: str, openrouter_key: str) -> str:
    """Call OpenRouter → google/gemini-2.5-flash and return the completion text."""
    url = f"{LLM_API_BASE}/chat/completions"
    headers = {
        "Authorization": f"Bearer {openrouter_key}",
        "Content-Type":  "application/json",
        "HTTP-Referer":  "https://github.com/BCR-AgentOn",
        "X-Title":       "BCR-AgentOn-DealWork",
    }
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {
                "role":    "system",
                "content": (
                    "You are an expert professional freelancer. "
                    "Deliver high-quality, well-structured work that fully satisfies the client's brief. "
                    "Be thorough, accurate, and professional."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 4096,
        "temperature": 0.7,
    }
    r = requests.post(url, headers=headers, json=payload, timeout=120)
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"].strip()


def build_prompt(job: dict) -> str:
    """Construct a detailed work prompt from a job record."""
    title       = job.get("title",       "Untitled Task")
    description = job.get("description", "No description provided.")
    job_type    = job.get("type",        "general")
    budget      = job.get("budget",      "unknown")
    currency    = job.get("currency",    "USDC")
    tags        = ", ".join(job.get("tags", []))

    return (
        f"# Job Brief\n\n"
        f"**Title**: {title}\n"
        f"**Type**: {job_type}\n"
        f"**Budget**: {budget} {currency}\n"
        f"**Tags**: {tags or 'none'}\n\n"
        f"## Description\n\n{description}\n\n"
        f"## Instructions\n\n"
        f"Complete this task fully and professionally. "
        f"Structure your deliverable clearly with headings, bullet points, or code blocks as appropriate. "
        f"Do not add disclaimers — just deliver the work."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Budget safety guard
# ─────────────────────────────────────────────────────────────────────────────
def parse_budget(job: dict) -> float:
    """Extract numeric budget value from job dict."""
    raw = job.get("budget", 0)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def within_budget(job: dict) -> bool:
    b = parse_budget(job)
    return BUDGET_MIN <= b <= BUDGET_MAX


# ─────────────────────────────────────────────────────────────────────────────
# Logging helpers
# ─────────────────────────────────────────────────────────────────────────────
def ensure_output_dir() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for f in (SUBMISSIONS_MD, PAYOUTS_MD):
        if not f.exists():
            f.write_text(
                f"# DealWork {f.stem.replace('-', ' ').title()}\n\n"
                f"_Auto-generated by dealwork_agent.py_\n\n---\n\n",
                encoding="utf-8",
            )


def log_submission(job: Job, deliverable: str) -> None:
    ts  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    jid = job.id
    ttl = job.title
    bgt = job.reward_usd
    cur = "USDC"
    preview = (deliverable[:300] + "…") if len(deliverable) > 300 else deliverable

    entry = (
        f"## [{ttl}] — Job `{jid}`\n"
        f"- **Submitted**: {ts}\n"
        f"- **Budget**: {bgt} {cur}\n"
        f"- **Deliverable preview**:\n\n"
        f"```\n{preview}\n```\n\n---\n\n"
    )
    with SUBMISSIONS_MD.open("a", encoding="utf-8") as fh:
        fh.write(entry)


def log_payout(job: Job, response: dict) -> None:
    ts     = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    jid    = job.id
    ttl    = job.title
    bgt    = job.reward_usd
    cur    = "USDC"
    status = response.get("status", "unknown")
    payout = response.get("payout") or response.get("amount") or bgt
    tx     = response.get("tx_hash") or response.get("transaction") or "pending"

    entry = (
        f"## [{ttl}] — Job `{jid}`\n"
        f"- **Time**: {ts}\n"
        f"- **Status**: {status}\n"
        f"- **Expected payout**: {payout} {cur}\n"
        f"- **TX**: {tx}\n\n---\n\n"
    )
    with PAYOUTS_MD.open("a", encoding="utf-8") as fh:
        fh.write(entry)

    from job_scoring import get_client_id_from_raw
    client_id = get_client_id_from_raw(job.raw)

    # Log to unified payouts database
    record_payout(
        platform="dealwork",
        job_id=str(jid),
        title=ttl,
        category=job.category,
        reward_usd=float(payout),
        status=status,
        estimated_minutes=job.estimated_minutes,
        notes=f"TX: {tx}",
        client_id=client_id
    )


def sync_payout_statuses(jobs: list[dict]):
    """Sync remote state of our dealwork jobs into payouts.json."""
    payouts_file = OUTPUT_DIR / "payouts.json"
    if not payouts_file.exists():
        return
    try:
        payouts = json.loads(payouts_file.read_text(encoding="utf-8"))
    except Exception:
        return
        
    known_ids = {str(p.get("job_id")) for p in payouts if p.get("platform") == "dealwork"}
    if not known_ids:
        return
        
    from job_scoring import get_client_id_from_raw
    for j in jobs:
        jid = str(j.get("id"))
        if jid in known_ids:
            status = j.get("status", "unknown")
            client_id = get_client_id_from_raw(j)
            record_payout(
                platform="dealwork",
                job_id=jid,
                title=j.get("title", ""),
                category=j.get("type", "other") or "other",
                reward_usd=parse_budget(j),
                status=status,
                client_id=client_id
            )


# ─────────────────────────────────────────────────────────────────────────────
# Git push
# ─────────────────────────────────────────────────────────────────────────────
def git_push(message: str = "chore: dealwork earn loop update") -> None:
    """Stage all changes in AgentOn root and push to origin."""
    try:
        subprocess.run(["git", "add", "-A"],            cwd=AGENTON_ROOT, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", message],cwd=AGENTON_ROOT, check=True, capture_output=True)
        subprocess.run(["git", "push"],                 cwd=AGENTON_ROOT, check=True, capture_output=True)
        log.info("Git push OK: %s", message)
    except subprocess.CalledProcessError as exc:
        # "nothing to commit" is not an error
        stderr = exc.stderr.decode(errors="replace") if exc.stderr else ""
        if "nothing to commit" in stderr or "nothing added" in stderr:
            log.debug("Git: nothing to commit.")
        else:
            log.warning("Git push failed: %s", stderr)


# ─────────────────────────────────────────────────────────────────────────────
# Job scanning
# ─────────────────────────────────────────────────────────────────────────────
def fetch_jobs(client: DealworkClient) -> list[dict]:
    """Return combined list from /jobs/matching + /jobs?status=open."""
    jobs: list[dict] = []
    seen_ids: set[str] = set()

    # 1. Matched jobs
    try:
        matched = client.get("/jobs/matching")
        if isinstance(matched, list):
            for j in matched:
                jid = j.get("id")
                if jid and jid not in seen_ids:
                    jobs.append(j)
                    seen_ids.add(jid)
        log.info("Matching jobs: %d", len(jobs))
    except requests.HTTPError as exc:
        log.warning("GET /jobs/matching failed: %s", exc)

    # 2. All open jobs (fallback / supplement)
    try:
        open_jobs = client.get("/jobs", params={"limit": 50})
        if isinstance(open_jobs, list):
            for j in open_jobs:
                jid = j.get("id")
                if jid and jid not in seen_ids:
                    jobs.append(j)
                    seen_ids.add(jid)
        elif isinstance(open_jobs, dict):
            # Some APIs wrap in {data: [...]}
            for j in open_jobs.get("data", []):
                jid = j.get("id")
                if jid and jid not in seen_ids:
                    jobs.append(j)
                    seen_ids.add(jid)
        log.info("Total unique jobs fetched: %d", len(jobs))
    except requests.HTTPError as exc:
        log.warning("GET /jobs failed: %s", exc)

    return jobs


def get_client_id(job: dict) -> str:
    """Extract client / poster identifier from job record."""
    poster = job.get("poster")
    if isinstance(poster, dict):
        pid = poster.get("id") or poster.get("accountId")
        if pid:
            return str(pid)
    for field in ["poster_id", "client_id", "clientId", "posterId", "owner_id", "owner"]:
        val = job.get(field)
        if val:
            return str(val)
    return "unknown_client"


def filter_jobs(jobs: list[dict], attempted: set[str], agent_id: str) -> list[dict]:
    """Keep jobs that are open, within budget, not yet attempted, and respect client cooldown."""
    # Find clients of currently claimed/delivered jobs that belong to us
    active_clients = set()
    for j in jobs:
        jid = j.get("id", "")
        status = j.get("status", "")
        # If we have already attempted this job, and it's still in claimed/delivered status, it's pending approval
        if jid in attempted and status in ("claimed", "delivered", "working"):
            client_id = get_client_id(j)
            if client_id != "unknown_client":
                active_clients.add(client_id)
                log.info("Client %s has a pending job %s (status: %s) — applying cooldown.", client_id, jid, status)

    eligible = []
    for j in jobs:
        jid    = j.get("id", "")
        status = j.get("status", "open")
        jtype  = j.get("type", "") or ""
        tags   = [t.lower() for t in j.get("tags", [])]

        if jid in attempted:
            continue
        if status not in ("open", "available", ""):
            continue
        if not within_budget(j):
            log.debug("Skip job %s — budget %.2f out of range", jid, parse_budget(j))
            continue

        # Client cooldown check
        client_id = get_client_id(j)
        if client_id != "unknown_client" and client_id in active_clients:
            log.info("Skip job %s — client %s cooldown active", jid, client_id)
            continue

        # Only take jobs our capabilities cover
        task_type = jtype.lower()
        all_labels = [task_type] + tags
        if not any(cap in lbl for cap in CAPABILITIES for lbl in all_labels):
            log.debug("Skip job %s — no capability match (type=%s)", jid, jtype)
            continue

        eligible.append(j)

    return eligible


# ─────────────────────────────────────────────────────────────────────────────
# Core earn loop
# ─────────────────────────────────────────────────────────────────────────────
def process_job(client: DealworkClient, job_obj: Job, openrouter_key: str) -> bool:
    """
    Claim → generate deliverable → deliver.
    Returns True on success, False on any failure.
    """
    jid   = job_obj.id
    title = job_obj.title
    bgt   = job_obj.reward_usd
    cur   = "USDC"

    log.info("── Processing job %s | '%s' | %.2f %s ──", jid, title, bgt, cur)

    # 1. Claim
    try:
        claim_resp = client.post(f"/jobs/{jid}/claim")
        log.info("Claimed job %s: %s", jid, claim_resp.get("status", "ok"))
    except requests.HTTPError as exc:
        sc = exc.response.status_code
        if sc in (409, 423):
            log.info("Job %s already claimed (HTTP %s) — skipping.", jid, sc)
        else:
            log.error("Claim failed for job %s: %s", jid, exc)
        return False

    # 2. Post "working on it" chat message
    try:
        client.post_chat(jid, "👋 BCR-AgentOn here — I've claimed this task and will deliver shortly.")
    except Exception:
        pass  # Chat is best-effort

    # 3. Generate deliverable using unified execution helper
    try:
        deliverable = execute_job_with_llm(job_obj, openrouter_key)
        log.info("Deliverable generated (%d chars) for job %s", len(deliverable), jid)
    except Exception as exc:
        log.error("LLM generation failed for job %s: %s", jid, exc)
        # Unclaim / abandon — no-op if endpoint doesn't exist
        try:
            client.patch(f"/jobs/{jid}", {"status": "open"})
        except Exception:
            pass
        return False

    # 4. Deliver via PATCH
    try:
        deliver_resp = client.patch(f"/jobs/{jid}", {
            "status":      "delivered",
            "deliverable": deliverable,
        })
        log.info("Delivered job %s — response status: %s", jid, deliver_resp.get("status"))
    except requests.HTTPError as exc:
        log.error("Deliver PATCH failed for job %s: %s %s", jid, exc.response.status_code, exc.response.text[:200])
        return False

    # 5. Log
    log_submission(job_obj, deliverable)
    log_payout(job_obj, deliver_resp)

    # 6. Post completion message
    try:
        payout_hint = deliver_resp.get("payout") or bgt
        client.post_chat(jid, f"✅ Deliverable submitted! Expected payout: {payout_hint} {cur}")
    except Exception:
        pass

    log.info("✅ Job %s complete — payout pending: %.2f %s", jid, bgt, cur)
    return True


def earn_loop(
    client: DealworkClient,
    openrouter_key: str,
    max_loops: int = MAX_LOOPS,
) -> None:
    """Main autonomous loop: scan → filter → process → git push → sleep → repeat."""
    attempted: set[str] = set()
    loop_count = 0

    log.info("🚀 DealWork earn loop started. max_loops=%s", max_loops or "∞")

    while True:
        loop_count += 1
        log.info("═══ Loop #%d ═══", loop_count)

        try:
            jobs = fetch_jobs(client)
            # Sync remote state of known jobs to payouts.json
            sync_payout_statuses(jobs)
        except Exception as exc:
            log.error("Failed to fetch jobs: %s", exc)
            jobs = []

        eligible = filter_jobs(jobs, attempted, client.agent_id)
        log.info("Eligible jobs this loop: %d", len(eligible))

        submitted_this_loop = 0
        for job in eligible:
            jid = job.get("id")
            attempted.add(jid)  # Mark before processing to avoid retries on error

            # ROI Scoring check
            raw_desc = job.get("description", job.get("title", ""))
            job_obj = Job(
                id=str(jid),
                platform="dealwork",
                title=job.get("title", "Untitled"),
                description=raw_desc,
                reward_usd=parse_budget(job),
                raw=job
            )
            try:
                score, evaluated_job = score_job(job_obj, openrouter_key)
                log.info("Job %s scored: %s (Category: %s, Complexity: %s, Ambiguity: %s)", jid, score, evaluated_job.category, evaluated_job.complexity, evaluated_job.ambiguity)
                
                # Check cross-agent category busy lock
                if is_category_busy(evaluated_job.category, str(jid)):
                    log.info("Skip job %s — category '%s' is currently busy with another active job.", jid, evaluated_job.category)
                    continue
                    
                if score < 0.4:
                    log.info("Skip job %s — score %s is below threshold 0.4", jid, score)
                    continue
            except Exception as e:
                log.error("Scoring failed for job %s: %s", jid, e)
                continue

            success = process_job(client, evaluated_job, openrouter_key)
            if success:
                submitted_this_loop += 1
                time.sleep(SUBMIT_DELAY)

        if submitted_this_loop > 0:
            git_push(f"chore: dealwork {submitted_this_loop} job(s) delivered — loop #{loop_count}")
        else:
            log.info("No jobs processed this loop.")

        if max_loops and loop_count >= max_loops:
            log.info("Reached max_loops=%d — exiting.", max_loops)
            break

        log.info("Sleeping %ds before next loop …\n", LOOP_DELAY)
        time.sleep(LOOP_DELAY)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    log.info("DealWork Agent starting …")
    ensure_output_dir()

    # Load credentials
    env = load_env()
    openrouter_key = env.get("OPENROUTER_API_KEY", "")
    if not openrouter_key:
        raise RuntimeError("OPENROUTER_API_KEY not found in bot.env")

    # Onboard / verify registration
    agent_id, agent_secret, bearer_token = onboard_agent(env)

    # Build client
    client = DealworkClient(agent_id, agent_secret, bearer_token)

    # Kick off earn loop
    earn_loop(client, openrouter_key, max_loops=MAX_LOOPS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Interrupted by user — exiting cleanly.")
    except Exception as exc:
        log.exception("Fatal error: %s", exc)
        sys.exit(1)
