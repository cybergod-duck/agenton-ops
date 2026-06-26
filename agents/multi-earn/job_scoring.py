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
def calculate_score(job: Job) -> float:
    # 1. Platform Trust
    # DealWork is fully verified and stable -> 1.2
    # BountyBook is stable -> 1.0
    # Claw Earn has on-chain staking and manual verification friction -> 0.9
    # ugig is a standard Supabase marketplace -> 1.0
    platform_trusts = {
        "dealwork": 1.2,
        "bountybook": 1.0,
        "claw earn": 0.9,
        "claw": 0.9,
        "ugig": 1.0
    }
    p_trust = platform_trusts.get(job.platform.lower(), 1.0)
    
    # 2. Fit Weight
    # Coding, AI, and data extraction/processing tasks have the highest automation fit
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
    score = (job.reward_usd / (est_mins + 5)) * (1.0 - job.ambiguity) * p_trust * f_weight
    return round(score, 4)

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
