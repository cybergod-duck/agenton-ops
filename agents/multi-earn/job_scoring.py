#!/usr/bin/env python3
"""
job_scoring.py — ROI-based job scoring module for Multi-Earn agents
==================================================================
Evaluates bounties / gigs across platforms (BountyBook, Claw Earn, DealWork, ugig)
using the ROI scoring formula:
  score = (payout / (estimated_minutes + 5)) * (1 - ambiguity) * platform_trust * fit_weight
"""

import os
import sys
import re
import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
import requests
from datetime import datetime, timezone

# ── Config & Paths ────────────────────────────────────────────────────────────
ROOT_DIR        = Path(r"C:\BC RESEARCH\AI_FACTORY")
AGENTON_DIR     = ROOT_DIR / "AgentOn"
CACHE_FILE      = AGENTON_DIR / "outputs" / "multi-earn" / "scored_jobs_cache.json"

LLM_MODEL       = "google/gemini-2.5-flash"
LLM_API_BASE    = "https://openrouter.ai/api/v1"

log = logging.getLogger("job_scoring")

@dataclass
class Job:
    id: str
    platform: str
    title: str
    description: str
    reward_usd: float
    estimated_minutes: int | None = None
    category: str = "other"
    ambiguity: float = 0.5
    complexity: str = "medium"
    raw: dict = None

# ── Cache Management ──────────────────────────────────────────────────────────
_cache: dict = {}

def load_cache():
    global _cache
    if CACHE_FILE.exists():
        try:
            _cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning(f"Error loading scored jobs cache: {e}")
            _cache = {}
    else:
        _cache = {}

def save_cache():
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        CACHE_FILE.write_text(json.dumps(_cache, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning(f"Error saving scored jobs cache: {e}")

# ── LLM Feature Extraction ────────────────────────────────────────────────────
def clean_json_text(text: str) -> str:
    text = text.strip()
    for fence in ("```json", "```"):
        if text.startswith(fence):
            text = text[len(fence):]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()

def extract_features_with_llm(title: str, description: str, openrouter_key: str) -> dict:
    """Use Gemini-2.5-flash to estimate job duration, category, complexity, and ambiguity."""
    prompt = f"""Analyze this freelance job/bounty posting:
Title: {title}
Description: {description}

Extract the following details as a JSON object:
1. "estimated_minutes": integer (estimate how many minutes it will take a highly capable AI agent to complete the task. Default to 15 if not clear).
2. "category": string ("coding" | "writing" | "data" | "marketing" | "other").
3. "complexity": string ("easy" | "medium" | "hard").
4. "ambiguity": float between 0.0 and 1.0 (0.0 means requirements are crystal clear and deliverables are perfectly specified, 1.0 means extremely vague, contradictory, or requires human-in-the-loop verification).

Format the output as raw JSON only. Do not add markdown or code fences.
"""
    url = f"{LLM_API_BASE}/chat/completions"
    headers = {
        "Authorization": f"Bearer {openrouter_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/BCR-AgentOn",
        "X-Title": "BCR-AgentOn-JobScoring",
    }
    payload = {
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        "max_tokens": 500,
        "temperature": 0.1,
    }
    
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=20)
        if r.status_code == 200:
            content = r.json()["choices"][0]["message"]["content"].strip()
            data = json.loads(clean_json_text(content))
            return {
                "estimated_minutes": int(data.get("estimated_minutes", 15)),
                "category": str(data.get("category", "other")).lower(),
                "complexity": str(data.get("complexity", "medium")).lower(),
                "ambiguity": float(data.get("ambiguity", 0.5))
            }
        else:
            log.warning(f"OpenRouter returned status {r.status_code}: {r.text}")
    except Exception as e:
        log.warning(f"Failed to extract features with LLM: {e}")
        
    # Safe defaults
    return {
        "estimated_minutes": 15,
        "category": "other",
        "complexity": "medium",
        "ambiguity": 0.5
    }

# ── Score Computation ─────────────────────────────────────────────────────────
def get_historical_rates(platform: str, category: str) -> tuple[float, float]:
    """Retrieve historical accept rates and realized payout ratios for this category/platform."""
    payouts_file = Path(r"C:\BC RESEARCH\AI_FACTORY\AgentOn\outputs\multi-earn\payouts.json")
    if not payouts_file.exists():
        return 1.0, 1.0
    try:
        data = json.loads(payouts_file.read_text(encoding="utf-8"))
    except Exception:
        return 1.0, 1.0
        
    def is_resolved(status: str) -> bool:
        s = str(status).lower().strip()
        return any(x in s for x in ("paid", "completed", "rejected", "failed", "success", "approved"))
        
    def is_accepted(status: str) -> bool:
        s = str(status).lower().strip()
        return any(x in s for x in ("paid", "completed", "accepted", "success", "approved"))

    resolved_jobs = [p for p in data if p.get("platform", "").lower() == platform.lower()
                     and p.get("category", "").lower() == category.lower()
                     and is_resolved(p.get("status", ""))]
    accepted_jobs = [p for p in resolved_jobs if is_accepted(p.get("status", ""))]
    
    accept_rate = 1.0
    if resolved_jobs:
        accept_rate = len(accepted_jobs) / len(resolved_jobs)
        
    platform_resolved_jobs = [p for p in data if p.get("platform", "").lower() == platform.lower()
                              and is_resolved(p.get("status", ""))]
    platform_paid_jobs = [p for p in platform_resolved_jobs if is_accepted(p.get("status", ""))]
    
    realized_ratio = 1.0
    if platform_resolved_jobs:
        promised_sum = sum(float(p.get("reward_usd", 0)) for p in platform_resolved_jobs)
        paid_sum = sum(float(p.get("reward_usd", 0)) for p in platform_paid_jobs)
        if promised_sum > 0:
            realized_ratio = paid_sum / promised_sum
            
    return round(accept_rate, 4), round(realized_ratio, 4)

def get_client_id_from_raw(raw_dict: dict) -> str:
    if not raw_dict or not isinstance(raw_dict, dict):
        return "unknown_client"
    # Try all common fields
    for field in ("client_id", "clientId", "created_by", "creator_id", "userId", "user_id", "poster_id", "posterId", "owner_id", "owner"):
        val = raw_dict.get(field)
        if val:
            if isinstance(val, dict):
                inner_id = val.get("id") or val.get("accountId")
                if inner_id:
                    return str(inner_id)
            return str(val)
    # Check nested poster field (for DealWork)
    poster = raw_dict.get("poster")
    if isinstance(poster, dict):
        pid = poster.get("id") or poster.get("accountId")
        if pid:
            return str(pid)
    return "unknown_client"

def get_client_reputation_multiplier(platform: str, client_id: str) -> float:
    """Combine client's accept ratio and rating into a single multiplier."""
    if not client_id or client_id == "unknown_client":
        return 1.0
        
    clients_file = AGENTON_DIR / "outputs" / "multi-earn" / "clients.json"
    if not clients_file.exists():
        return 1.0
        
    try:
        data = json.loads(clients_file.read_text(encoding="utf-8"))
        clients = data.get("clients", {})
        key = f"{platform.lower().strip()}:{client_id.strip()}"
        if key in clients:
            c = clients[key]
            jobs_total = int(c.get("jobs_total", 0))
            jobs_accepted = int(c.get("jobs_accepted", 0))
            rating = float(c.get("avg_rating", 0.0))
            
            if jobs_total < 3:
                return 1.0 # not enough data
                
            accept_rate = jobs_accepted / jobs_total
            
            if accept_rate >= 0.9 and (rating >= 4.5 or rating == 0.0):
                return 1.15
            if accept_rate >= 0.75:
                return 1.05
            if accept_rate <= 0.4 or (rating > 0.0 and rating <= 3.0):
                return 0.4
            if accept_rate <= 0.6:
                return 0.7
    except Exception:
        pass
    return 1.0

def token_efficiency_multiplier(platform: str, category: str) -> float:
    """Retrieve token efficiency ratio and apply dynamic multiplier."""
    try:
        from payouts_tracker import get_token_efficiency_ratio
        ratio = get_token_efficiency_ratio(platform, category)
    except Exception:
        ratio = 0.0
        
    if ratio == 0:
        return 1.0
    if ratio <= 0.01:
        return 1.05  # very efficient, small boost
    if ratio >= 0.05:
        return 0.8   # too expensive, penalize
    return 1.0

def calculate_score(job: Job) -> float:
    # 1. Platform Trust
    platform_trusts = {
        "dealwork": 1.2,
        "bountybook": 1.0,
        "claw earn": 1.1,
        "claw": 1.1,
        "ugig": 0.9
    }
    p_trust = platform_trusts.get(job.platform.lower(), 1.0)
    
    # 2. Fit Weight
    fit_weights = {
        "coding": 1.3,
        "code": 1.3,
        "data": 1.3,
        "research": 1.2,
        "writing": 1.1,
        "content": 1.1,
        "marketing": 0.8,
        "social": 0.8,
        "other": 0.9
    }
    f_weight = fit_weights.get(job.category.lower(), 0.9)
    
    # 3. Score Formula
    est_mins = job.estimated_minutes if job.estimated_minutes else 15
    base_score = (job.reward_usd / (est_mins + 5)) * (1.0 - job.ambiguity) * p_trust * f_weight
    
    # Apply outcome-aware win-rate multipliers
    accept_rate, realized_ratio = get_historical_rates(job.platform, job.category)
    score = base_score * (0.5 + 0.5 * accept_rate) * realized_ratio
    
    # Apply client reputation & token efficiency multipliers
    client_id = get_client_id_from_raw(job.raw)
    client_mult = get_client_reputation_multiplier(job.platform, client_id)
    token_mult = token_efficiency_multiplier(job.platform, job.category)
    score = score * client_mult * token_mult
    
    return round(score, 4)

def execute_job_with_llm(job: Job, openrouter_key: str) -> str:
    """
    Standardised function to execute a job using Gemini-2.5-flash on OpenRouter.
    Retrieves the correct prompt template based on category, queries LLM,
    extracts token usage and cost from the response, logs it via payouts_tracker,
    and returns the final deliverable.
    """
    from prompt_templates import get_template
    from payouts_tracker import record_token_usage
    
    template = get_template(job.category)
    prompt = template.format(
        title=job.title,
        reward=job.reward_usd,
        description=job.description
    )
    
    url = f"{LLM_API_BASE}/chat/completions"
    headers = {
        "Authorization": f"Bearer {openrouter_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/BCR-AgentOn",
        "X-Title": "BCR-AgentOn-WorkExecution",
    }
    payload = {
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4000,
        "temperature": 0.5,
    }
    
    r = requests.post(url, headers=headers, json=payload, timeout=120)
    r.raise_for_status()
    resp_data = r.json()
    content = resp_data["choices"][0]["message"]["content"].strip()
    
    usage = resp_data.get("usage", {})
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    
    # Gemini-2.5-flash OpenRouter costs (Input: $0.075/1M, Output: $0.30/1M)
    cost_usd = (prompt_tokens * 0.075 / 1_000_000) + (completion_tokens * 0.30 / 1_000_000)
    
    try:
        record_token_usage(
            platform=job.platform,
            job_id=job.id,
            model=LLM_MODEL,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd
        )
    except Exception as e:
        log.warning(f"Failed to record token usage: {e}")
        
    return content

def score_job(job: Job, openrouter_key: str) -> tuple[float, Job]:
    """Score a job, loading from cache if available, or calling LLM + saving."""
    load_cache()
    
    cache_key = f"{job.platform}:{job.id}"
    if cache_key in _cache:
        cached = _cache[cache_key]
        job.estimated_minutes = cached.get("estimated_minutes")
        job.category = cached.get("category", "other")
        job.ambiguity = cached.get("ambiguity", 0.5)
        job.complexity = cached.get("complexity", "medium")
        score = calculate_score(job)
        return score, job

    # Call LLM to extract features
    features = extract_features_with_llm(job.title, job.description, openrouter_key)
    
    job.estimated_minutes = features["estimated_minutes"]
    job.category = features["category"]
    job.ambiguity = features["ambiguity"]
    job.complexity = features["complexity"]
    
    score = calculate_score(job)
    
    # Save to cache
    _cache[cache_key] = {
        "estimated_minutes": job.estimated_minutes,
        "category": job.category,
        "ambiguity": job.ambiguity,
        "complexity": job.complexity,
        "score": score,
        "title": job.title,
        "reward_usd": job.reward_usd,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    save_cache()
    
    return score, job
