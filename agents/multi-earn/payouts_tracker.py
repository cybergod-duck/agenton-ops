# payouts_tracker.py — Centralized payout tracking and analytics for Multi-Earn
"""
Centralized payout logging, outcome tracking, and cross-agent concurrency locks.
Tracks expected vs realized earnings, success rates, and category-level locks.
"""

import os
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(r"C:\BC RESEARCH\AI_FACTORY")
AGENTON_DIR = ROOT_DIR / "AgentOn"
PAYOUTS_JSON = AGENTON_DIR / "outputs" / "multi-earn" / "payouts.json"

log = logging.getLogger("payouts_tracker")

def get_cached_category(platform: str, job_id: str) -> str:
    """Read the category directly from the scored jobs cache file."""
    cache_file = AGENTON_DIR / "outputs" / "multi-earn" / "scored_jobs_cache.json"
    if cache_file.exists():
        try:
            cache = json.loads(cache_file.read_text(encoding="utf-8"))
            key = f"{platform}:{job_id}"
            if key in cache:
                return cache[key].get("category", "other")
        except Exception:
            pass
    return "other"

def record_payout(
    platform: str,
    job_id: str,
    title: str,
    category: str,
    reward_usd: float,
    status: str,
    estimated_minutes: int | None = None,
    actual_minutes: int | None = None,
    notes: str = ""
):
    """
    Record or update a payout entry in the unified payouts.json database.
    Performs field merging so existing values are preserved if not provided.
    """
    if not category or category == "other":
        category = get_cached_category(platform, job_id)
        
    PAYOUTS_JSON.parent.mkdir(parents=True, exist_ok=True)
    
    # Load existing
    payouts = []
    if PAYOUTS_JSON.exists():
        try:
            payouts = json.loads(PAYOUTS_JSON.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning(f"Error loading payouts.json, recreating: {e}")
            payouts = []
            
    # Find matching entry
    found = False
    for p in payouts:
        if p.get("platform") == platform and str(p.get("job_id")) == str(job_id):
            p["status"] = status
            if title:
                p["title"] = title
            if category:
                p["category"] = category
            if reward_usd > 0:
                p["reward_usd"] = reward_usd
            if estimated_minutes is not None:
                p["estimated_minutes"] = estimated_minutes
            if actual_minutes is not None:
                p["actual_minutes"] = actual_minutes
            if notes:
                p["notes"] = notes
            p["updated_at"] = datetime.now(timezone.utc).isoformat()
            
            # Record resolved_at time when entering final state
            if status in ("paid", "completed", "rejected", "failed") and not p.get("resolved_at"):
                p["resolved_at"] = datetime.now(timezone.utc).isoformat()
            found = True
            break
            
    if not found:
        now_str = datetime.now(timezone.utc).isoformat()
        payouts.append({
            "platform": platform,
            "job_id": job_id,
            "title": title,
            "category": category,
            "reward_usd": reward_usd,
            "status": status,
            "estimated_minutes": estimated_minutes,
            "actual_minutes": actual_minutes,
            "created_at": now_str,
            "updated_at": now_str,
            "resolved_at": now_str if status in ("paid", "completed", "rejected", "failed") else None,
            "notes": notes
        })
        
    try:
        PAYOUTS_JSON.write_text(json.dumps(payouts, indent=2), encoding="utf-8")
    except Exception as e:
        log.error(f"Failed to write payouts.json: {e}")

def is_category_busy(category: str, current_job_id: str = None) -> bool:
    """
    Check if another agent is currently claiming or working on a job in this category.
    Acts as a decentralized lock to avoid simultaneous category claims.
    """
    if not PAYOUTS_JSON.exists():
        return False
    try:
        payouts = json.loads(PAYOUTS_JSON.read_text(encoding="utf-8"))
    except Exception:
        return False
        
    now = datetime.now(timezone.utc)
    for p in payouts:
        # Check active states
        if p.get("category", "").lower().strip() == category.lower().strip():
            if p.get("status") in ("pending", "applied", "accepted", "submitted"):
                # Ignore checking the current job's own lock
                if current_job_id and str(p.get("job_id")) == str(current_job_id):
                    continue
                # 12-hour expiry threshold on lock
                created_str = p.get("created_at")
                if created_str:
                    try:
                        created = datetime.fromisoformat(created_str)
                        if (now - created).total_seconds() < 12 * 3600:
                            return True
                    except Exception:
                        pass
    return False
