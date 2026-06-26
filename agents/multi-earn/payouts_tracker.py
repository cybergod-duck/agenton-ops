# payouts_tracker.py — Centralized payout tracking and analytics for Multi-Earn
"""
Centralized payout logging, outcome tracking, token economics, and cross-agent locks.
Tracks expected vs realized earnings, success rates, token usage, and category-level locks.
"""

import os
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT_DIR = Path(r"C:\BC RESEARCH\AI_FACTORY")
AGENTON_DIR = ROOT_DIR / "AgentOn"
PAYOUTS_JSON = AGENTON_DIR / "outputs" / "multi-earn" / "payouts.json"
CLIENTS_JSON = AGENTON_DIR / "outputs" / "multi-earn" / "clients.json"
POSTMORTEMS_DIR = AGENTON_DIR / "outputs" / "multi-earn" / "postmortems"

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
    notes: str = "",
    client_id: str = "",
    client_rating: float = 0.0,
    client_feedback: str = ""
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
            if client_id:
                p["client_id"] = client_id
                
            # If client_id is available, denormalize a client snapshot
            if p.get("client_id"):
                snap = get_client_snapshot(platform, p["client_id"])
                if client_rating > 0:
                    snap["rating"] = client_rating
                if client_feedback:
                    snap["last_feedback"] = client_feedback
                p["client_snapshot"] = snap
                
            p["updated_at"] = datetime.now(timezone.utc).isoformat()
            
            # Record resolved_at time when entering final state
            if status in ("paid", "completed", "rejected", "failed") and not p.get("resolved_at"):
                p["resolved_at"] = datetime.now(timezone.utc).isoformat()
            found = True
            break
            
    if not found:
        now_str = datetime.now(timezone.utc).isoformat()
        snap = {}
        if client_id:
            snap = get_client_snapshot(platform, client_id)
            if client_rating > 0:
                snap["rating"] = client_rating
            if client_feedback:
                snap["last_feedback"] = client_feedback
                
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
            "notes": notes,
            "client_id": client_id or "unknown_client",
            "client_snapshot": snap
        })
        
    try:
        PAYOUTS_JSON.write_text(json.dumps(payouts, indent=2), encoding="utf-8")
        
        # Trigger client reputation rebuilding
        if client_id:
            rebuild_client_reputation()
            
        # Trigger failure forensics if status shows rejection/failure
        s_lower = status.lower()
        if "reject" in s_lower or "fail" in s_lower:
            archive_failure_postmortem(
                platform=platform,
                job_id=job_id,
                prompt="",
                output="",
                feedback=client_feedback or notes
            )
    except Exception as e:
        log.error(f"Failed to write payouts.json: {e}")

def record_token_usage(
    platform: str,
    job_id: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: float
) -> None:
    """
    Append a token-usage record to the given job in payouts.json.
    If the job doesn't exist yet, initialize a stub entry.
    Also recomputes token_metrics.total_cost_usd.
    """
    PAYOUTS_JSON.parent.mkdir(parents=True, exist_ok=True)
    payouts = []
    if PAYOUTS_JSON.exists():
        try:
            payouts = json.loads(PAYOUTS_JSON.read_text(encoding="utf-8"))
        except Exception:
            payouts = []
            
    now_str = datetime.now(timezone.utc).isoformat()
    call_record = {
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cost_usd": cost_usd,
        "timestamp": now_str
    }
    
    found = False
    for p in payouts:
        if p.get("platform") == platform and str(p.get("job_id")) == str(job_id):
            metrics = p.get("token_metrics", {})
            calls = metrics.get("llm_calls", [])
            calls.append(call_record)
            metrics["llm_calls"] = calls
            metrics["total_cost_usd"] = sum(float(c.get("cost_usd", 0.0)) for c in calls)
            p["token_metrics"] = metrics
            p["updated_at"] = now_str
            found = True
            break
            
    if not found:
        payouts.append({
            "platform": platform,
            "job_id": job_id,
            "title": "Stub (Token Recording)",
            "category": "other",
            "reward_usd": 0.0,
            "status": "expected",
            "created_at": now_str,
            "updated_at": now_str,
            "resolved_at": None,
            "notes": "Initialized by record_token_usage",
            "client_id": "unknown_client",
            "client_snapshot": {},
            "token_metrics": {
                "llm_calls": [call_record],
                "total_cost_usd": cost_usd
            }
        })
        
    try:
        PAYOUTS_JSON.write_text(json.dumps(payouts, indent=2), encoding="utf-8")
    except Exception as e:
        log.error(f"Failed to write payouts.json inside record_token_usage: {e}")

def get_token_efficiency_ratio(platform: str, category: str) -> float:
    """
    Returns total_cost_usd / total_earned_usd for the given platform/category
    over the last 30 days. If total_earned_usd is 0, returns 0.0.
    """
    if not PAYOUTS_JSON.exists():
        return 0.0
    try:
        payouts = json.loads(PAYOUTS_JSON.read_text(encoding="utf-8"))
    except Exception:
        return 0.0
        
    now = datetime.now(timezone.utc)
    limit_date = now - timedelta(days=30)
    
    total_cost = 0.0
    total_earned = 0.0
    
    for p in payouts:
        if p.get("platform") != platform or p.get("category") != category:
            continue
            
        created_str = p.get("created_at")
        if created_str:
            try:
                created = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                if created < limit_date:
                    continue
            except Exception:
                pass
                
        metrics = p.get("token_metrics", {})
        total_cost += float(metrics.get("total_cost_usd", 0.0))
        
        status = str(p.get("status", "")).lower().strip()
        if any(x in status for x in ("paid", "completed", "accepted")):
            total_earned += float(p.get("reward_usd", 0.0))
            
    if total_earned > 0:
        return total_cost / total_earned
    return 0.0

def archive_failure_postmortem(
    platform: str,
    job_id: str,
    prompt: str,
    output: str,
    feedback: str
) -> None:
    """
    Writes a JSON dump containing post-mortem context of a failed/rejected job.
    """
    POSTMORTEMS_DIR.mkdir(parents=True, exist_ok=True)
    
    # Read details from payouts.json if available
    category = "other"
    client_id = "unknown_client"
    token_metrics = {}
    
    if PAYOUTS_JSON.exists():
        try:
            payouts = json.loads(PAYOUTS_JSON.read_text(encoding="utf-8"))
            for p in payouts:
                if p.get("platform") == platform and str(p.get("job_id")) == str(job_id):
                    category = p.get("category", "other")
                    client_id = p.get("client_id", "unknown_client")
                    token_metrics = p.get("token_metrics", {})
                    break
        except Exception:
            pass
            
    payload = {
        "platform": platform,
        "job_id": job_id,
        "category": category,
        "client_id": client_id,
        "status": "failed_or_rejected",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "prompt": prompt or "Not provided (archived via status change)",
        "output": output or "Not provided (archived via status change)",
        "feedback": feedback,
        "token_metrics": token_metrics
    }
    
    file_path = POSTMORTEMS_DIR / f"{platform}_{job_id}.json"
    try:
        file_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        log.info(f"Postmortem failure forenscis archived for {platform}:{job_id}")
    except Exception as e:
        log.error(f"Failed to write postmortem archive for {platform}:{job_id}: {e}")

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
            status = str(p.get("status", "")).lower().strip()
            if any(x in status for x in ("pending", "applied", "accepted", "submitted")):
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

def rebuild_client_reputation() -> None:
    """Scan payouts.json and update clients.json aggregates."""
    if not PAYOUTS_JSON.exists():
        return
    try:
        payouts = json.loads(PAYOUTS_JSON.read_text(encoding="utf-8"))
    except Exception:
        return
        
    clients = {}
    for p in payouts:
        client_id = p.get("client_id")
        if not client_id or client_id == "unknown_client":
            continue
            
        platform = p.get("platform", "").lower().strip()
        key = f"{platform}:{client_id}"
        
        if key not in clients:
            clients[key] = {
                "platform": platform,
                "client_id": client_id,
                "jobs_total": 0,
                "jobs_accepted": 0,
                "jobs_rejected": 0,
                "avg_rating": 0.0,
                "ratings_sum": 0.0,
                "ratings_count": 0,
                "last_seen": p.get("updated_at") or p.get("created_at") or ""
            }
            
        c = clients[key]
        c["jobs_total"] += 1
        
        status = str(p.get("status", "")).lower().strip()
        if any(x in status for x in ("paid", "completed", "accepted", "success", "approved")):
            c["jobs_accepted"] += 1
        elif any(x in status for x in ("rejected", "failed")):
            c["jobs_rejected"] += 1
            
        # If client_snapshot exists and has a rating
        snapshot = p.get("client_snapshot", {})
        rating = snapshot.get("rating")
        if rating is not None and rating > 0:
            c["ratings_sum"] += float(rating)
            c["ratings_count"] += 1
            
        # Update last_seen
        timestamp = p.get("updated_at") or p.get("created_at") or ""
        if timestamp > c["last_seen"]:
            c["last_seen"] = timestamp
            
    # Compute final averages
    for key, c in clients.items():
        if c["ratings_count"] > 0:
            c["avg_rating"] = round(c["ratings_sum"] / c["ratings_count"], 2)
        else:
            c["avg_rating"] = 0.0
        # Delete helper fields before saving
        del c["ratings_sum"]
        del c["ratings_count"]
        
    try:
        CLIENTS_JSON.write_text(json.dumps({"clients": clients}, indent=2), encoding="utf-8")
    except Exception as e:
        log.error(f"Failed to write clients.json: {e}")

def get_client_snapshot(platform: str, client_id: str) -> dict:
    """Get the current aggregated metrics for a client from clients.json."""
    if not CLIENTS_JSON.exists():
        return {}
    try:
        data = json.loads(CLIENTS_JSON.read_text(encoding="utf-8"))
        clients = data.get("clients", {})
        key = f"{platform.lower().strip()}:{client_id}"
        if key in clients:
            c = clients[key]
            return {
                "rating": c.get("avg_rating", 0.0),
                "jobs_total": c.get("jobs_total", 0),
                "jobs_paid": c.get("jobs_accepted", 0),
                "last_feedback": ""
            }
    except Exception:
        pass
    return {}

