# payouts_tracker.py — Centralized payout tracking and analytics for Multi-Earn
"""
Centralized payout logging, outcome tracking, token economics, and cross-agent locks.
Tracks expected vs realized earnings, success rates, token usage, and category-level locks.
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT_DIR = Path(r"C:\BC RESEARCH\AI_FACTORY")
# Add root directory to sys.path for importing core
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "core"))
sys.path.insert(0, str(ROOT_DIR / "crypto"))
from core.telegram_notify import notify_telegram, load_bot_env

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
    client_feedback: str = "",
    currency: str = "USDC"
):
    """
    Record or update a payout entry in the unified payouts.json database.
    Performs field merging so existing values are preserved if not provided.
    Triggers Telegram notifications and auto-withdrawals as needed.
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
    old_status = None
    old_currency = "USDC"
    for p in payouts:
        if p.get("platform") == platform and str(p.get("job_id")) == str(job_id):
            old_status = p.get("status")
            old_currency = p.get("currency") or "USDC"
            p["status"] = status
            if title:
                p["title"] = title
            if category:
                p["category"] = category
            if reward_usd > 0:
                p["reward_usd"] = reward_usd
            if currency:
                p["currency"] = currency
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
            "currency": currency,
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
        
        # Determine notification and withdrawal triggers
        is_new_claim = not found
        is_status_changed = found and old_status != status
        is_paid_transition = (found and old_status != "paid" and status == "paid") or (not found and status == "paid")
        
        # Send Telegram notifications
        if is_new_claim:
            msg = (
                f"✅ *Multi-Earn New Claim*\n"
                f"• *Platform*: `{platform}`\n"
                f"• *Job*: `{title}`\n"
                f"• *Job ID*: `{job_id}`\n"
                f"• *Category*: `{category}`\n"
                f"• *Status*: `{status}`\n"
                f"• *Reward*: `{reward_usd}` `{currency}`"
            )
            try:
                notify_telegram(msg)
            except Exception as e:
                log.warning(f"Failed to send Telegram notification: {e}")
        elif is_status_changed:
            msg = (
                f"🔄 *Multi-Earn Status Update*\n"
                f"• *Platform*: `{platform}`\n"
                f"• *Job*: `{title}`\n"
                f"• *Job ID*: `{job_id}`\n"
                f"• *Category*: `{category}`\n"
                f"• *Old Status*: `{old_status}`\n"
                f"• *New Status*: `{status}`\n"
                f"• *Reward*: `{reward_usd}` `{currency}`"
            )
            try:
                notify_telegram(msg)
            except Exception as e:
                log.warning(f"Failed to send Telegram notification: {e}")
                
        # Trigger auto-withdrawal immediately on paid transition
        if is_paid_transition:
            try:
                trigger_auto_withdrawal(platform, job_id, title, reward_usd, currency)
            except Exception as e:
                log.error(f"Auto-withdrawal trigger failed: {e}")
        
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

def send_onchain_evm_transfer(coin: str, amount_usd: float) -> str | None:
    keys = load_bot_env()
    pk = keys.get("AGENT_ETH_PRIVATE_KEY") or keys.get("BOUNTYBOOK_PRIVATE_KEY")
    recipient = keys.get("TREASURY_ADDRESS") or "0x0190C582b0eF8a4D27aaDbf73FEFc1f389bd1f5C"
    
    if not pk:
        log.warning("No AGENT_ETH_PRIVATE_KEY found for on-chain withdrawal.")
        return None
        
    try:
        from web3 import Web3
        w3 = Web3(Web3.HTTPProvider("https://mainnet.base.org"))
        account = w3.eth.account.from_key(pk)
        
        gas_price = int(w3.eth.gas_price * 1.2)
        if gas_price < w3.to_wei(0.05, 'gwei'):
            gas_price = w3.to_wei(0.05, 'gwei') # safe minimum floor
            
        coin_upper = coin.upper().strip()
        
        if coin_upper == "USDC":
            usdc_address = "0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913"
            usdc_abi = [
                {"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"},
                {"constant": False, "inputs": [{"name": "_to", "type": "address"}, {"name": "_value", "type": "uint256"}], "name": "transfer", "outputs": [{"name": "success", "type": "bool"}], "type": "function"},
                {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"}
            ]
            
            usdc_contract = w3.eth.contract(address=w3.to_checksum_address(usdc_address), abi=usdc_abi)
            usdc_balance_raw = usdc_contract.functions.balanceOf(account.address).call()
            
            if usdc_balance_raw > 0:
                tx = usdc_contract.functions.transfer(
                    w3.to_checksum_address(recipient),
                    usdc_balance_raw
                ).build_transaction({
                    'from': account.address,
                    'nonce': w3.eth.get_transaction_count(account.address),
                    'gas': 60000,
                    'gasPrice': gas_price,
                    'chainId': 8453
                })
                signed = w3.eth.account.sign_transaction(tx, pk)
                tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
                return w3.to_hex(tx_hash)
            else:
                log.warning("USDC withdrawal triggered but USDC balance is 0.")
                
        elif coin_upper == "ETH":
            eth_balance_wei = w3.eth.get_balance(account.address)
            gas_limit = 21000
            gas_cost = gas_limit * gas_price
            reserve_wei = w3.to_wei(0.001, 'ether')
            
            amount_to_send = eth_balance_wei - gas_cost - reserve_wei
            if amount_to_send > 0:
                tx = {
                    'nonce': w3.eth.get_transaction_count(account.address),
                    'to': w3.to_checksum_address(recipient),
                    'value': amount_to_send,
                    'gas': gas_limit,
                    'gasPrice': gas_price,
                    'chainId': 8453
                }
                signed = w3.eth.account.sign_transaction(tx, pk)
                tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
                return w3.to_hex(tx_hash)
            else:
                log.warning(f"ETH withdrawal triggered but ETH balance ({w3.from_wei(eth_balance_wei, 'ether')} ETH) is below minimum gas+reserve threshold.")
                
    except Exception as e:
        log.error(f"On-chain EVM transfer failed: {e}")
        
    return None

def send_onchain_sol_transfer() -> str | None:
    keys = load_bot_env()
    priv_b58 = keys.get("AGENT_SOL_PRIVATE_KEY")
    recipient_str = keys.get("SOL_ADDRESS")
    if not priv_b58 or not recipient_str:
        log.warning("Solana keys not configured in bot.env.")
        return None
        
    try:
        from solana.rpc.api import Client
        from solders.keypair import Keypair
        from solders.pubkey import Pubkey
        from solders.system_program import TransferParams, transfer
        from solders.transaction import Transaction
        
        client = Client("https://api.mainnet-beta.solana.com")
        sender_kp = Keypair.from_base58_string(priv_b58)
        receiver_pubkey = Pubkey.from_string(recipient_str)
        
        res = client.get_balance(sender_kp.pubkey())
        balance_lamports = res.value
        
        fee_lamports = 10000
        amount_lamports = balance_lamports - fee_lamports
        
        if amount_lamports <= 0:
            log.warning(f"Solana balance ({balance_lamports} lamports) too low for transfer.")
            return None
            
        blockhash = client.get_latest_blockhash().value.blockhash
        transfer_params = TransferParams(
            from_pubkey=sender_kp.pubkey(),
            to_pubkey=receiver_pubkey,
            lamports=amount_lamports
        )
        ix = transfer(transfer_params)
        tx = Transaction.new_signed_with_payer([ix], sender_kp.pubkey(), [sender_kp], blockhash)
        
        send_res = client.send_raw_transaction(bytes(tx))
        return str(send_res.value)
    except Exception as e:
        log.error(f"Solana on-chain transfer failed: {e}")
        return None

def send_onchain_btc_transfer() -> str | None:
    keys = load_bot_env()
    priv_hex = keys.get("AGENT_BTC_PRIVATE_KEY")
    from_addr = keys.get("AGENT_BTC_ADDRESS")
    to_addr = keys.get("BTC_ADDRESS")
    
    if not priv_hex or not from_addr or not to_addr:
        log.warning("Bitcoin keys not configured in bot.env.")
        return None
        
    try:
        import requests
        import cryptos
        
        utxo_url = f"https://blockstream.info/api/address/{from_addr}/utxo"
        r = requests.get(utxo_url, timeout=10)
        if r.status_code != 200:
            log.error(f"Failed to fetch BTC UTXOs: {r.status_code}")
            return None
            
        utxos = r.json()
        if not utxos:
            log.warning("No BTC UTXOs available to spend.")
            return None
            
        inputs = []
        total_balance = 0
        for utxo in utxos:
            inputs.append({
                'output': f"{utxo['txid']}:{utxo['vout']}",
                'value': utxo['value']
            })
            total_balance += utxo['value']
            
        fee = 3000
        amount_to_send = total_balance - fee
        if amount_to_send <= 0:
            log.warning(f"BTC balance ({total_balance} sats) too low to cover fee.")
            return None
            
        btc = cryptos.Bitcoin()
        outputs = [{'address': to_addr, 'value': amount_to_send}]
        tx = btc.mktx(inputs, outputs)
        
        for i in range(len(inputs)):
            tx = btc.sign(tx, i, priv_hex)
            
        raw_tx_hex = cryptos.serialize(tx)
        
        push_url = "https://blockstream.info/api/tx"
        push_res = requests.post(push_url, data=raw_tx_hex, timeout=15)
        if push_res.status_code == 200:
            return push_res.text.strip()
        else:
            log.error(f"Failed to broadcast BTC tx: {push_res.status_code} - {push_res.text}")
            return None
    except Exception as e:
        log.error(f"BTC on-chain transfer failed: {e}")
        return None

def send_onchain_doge_transfer() -> str | None:
    keys = load_bot_env()
    priv_hex = keys.get("AGENT_DOGE_PRIVATE_KEY")
    from_addr = keys.get("AGENT_DOGE_ADDRESS")
    to_addr = keys.get("DOGE_ADDRESS")
    
    if not priv_hex or not from_addr or not to_addr:
        log.warning("Dogecoin keys not configured in bot.env.")
        return None
        
    try:
        import requests
        import cryptos
        
        utxo_url = f"https://api.blockcypher.com/v1/doge/main/addrs/{from_addr}?unspentOnly=true"
        r = requests.get(utxo_url, timeout=10)
        if r.status_code != 200:
            log.error(f"Failed to fetch DOGE UTXOs: {r.status_code}")
            return None
            
        data = r.json()
        txrefs = data.get("txrefs", [])
        if not txrefs:
            log.warning("No DOGE UTXOs available to spend.")
            return None
            
        inputs = []
        total_balance = 0
        for ref in txrefs:
            if not ref.get("spent"):
                inputs.append({
                    'output': f"{ref['tx_hash']}:{ref['tx_output_n']}",
                    'value': ref['value']
                })
                total_balance += ref['value']
                
        fee = 150000000
        amount_to_send = total_balance - fee
        if amount_to_send <= 0:
            log.warning(f"DOGE balance ({total_balance} koinu) too low to cover fee.")
            return None
            
        doge = cryptos.Doge()
        outputs = [{'address': to_addr, 'value': amount_to_send}]
        tx = doge.mktx(inputs, outputs)
        
        for i in range(len(inputs)):
            tx = doge.sign(tx, i, priv_hex)
            
        raw_tx_hex = cryptos.serialize(tx)
        
        push_url = "https://api.blockcypher.com/v1/doge/main/txs/push"
        payload = {"tx": raw_tx_hex}
        push_res = requests.post(push_url, json=payload, timeout=15)
        if push_res.status_code in (200, 201):
            res_data = push_res.json()
            return res_data.get("tx", {}).get("hash")
        else:
            log.error(f"Failed to broadcast DOGE tx: {push_res.status_code} - {push_res.text}")
            return None
    except Exception as e:
        log.error(f"DOGE on-chain transfer failed: {e}")
        return None

def trigger_auto_withdrawal(platform: str, job_id: str, title: str, reward_usd: float, currency: str):
    keys = load_bot_env()
    coin = currency.upper().strip()
    
    target_address = keys.get("TREASURY_ADDRESS")
    if coin == "SOL":
        target_address = keys.get("SOL_ADDRESS")
    elif coin == "BTC":
        target_address = keys.get("BTC_ADDRESS")
    elif coin == "DOGE":
        target_address = keys.get("DOGE_ADDRESS")
        
    log.info(f"Triggering auto-withdrawal for {reward_usd} {coin} to {target_address}")
    
    tx_hash = None
    if coin in ("ETH", "USDC"):
        tx_hash = send_onchain_evm_transfer(coin, reward_usd)
    elif coin == "SOL":
        tx_hash = send_onchain_sol_transfer()
    elif coin == "BTC":
        tx_hash = send_onchain_btc_transfer()
    elif coin == "DOGE":
        tx_hash = send_onchain_doge_transfer()
        
    if tx_hash:
        msg = (
            f"📤 *Auto-Withdrawal Fired Successfully (On-Chain)*\n"
            f"• *Platform*: `{platform}`\n"
            f"• *Job*: `{title}`\n"
            f"• *Asset*: `{coin}`\n"
            f"• *Amount*: `{reward_usd}` USD equivalent\n"
            f"• *Recipient*: `{target_address}`\n"
            f"• *Tx Hash*: `{tx_hash}`"
        )
        notify_telegram(msg)
    else:
        msg = (
            f"⚠️ *Auto-Withdrawal Failed (On-Chain)*\n"
            f"• *Platform*: `{platform}`\n"
            f"• *Job*: `{title}`\n"
            f"• *Asset*: `{coin}`\n"
            f"• *Amount*: `{reward_usd}` USD equivalent\n"
            f"• *Recipient*: `{target_address}`\n"
            f"• *Error*: `Transaction signing failed, execution failed, or insufficient balance`"
        )
        notify_telegram(msg)



