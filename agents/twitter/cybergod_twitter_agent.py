#!/usr/bin/env python3
"""
cybergod Twitter Agent
Agent: @BC_Research_
Does two things:
  1. POST - Auto-posts when a quest is completed (reads from outputs/submissions-log.md)
  2. MONITOR - Watches AgentOn + FluxA Twitter for new quest announcements and writes to quests/active.md

Requires:
  pip install tweepy requests python-dotenv

Env vars needed in .env:
  X_API_KEY
  X_API_SECRET
  X_ACCESS_TOKEN
  X_ACCESS_SECRET
  X_BEARER_TOKEN
  GITHUB_TOKEN (to write back to repo)
"""

import os
import time
import tweepy
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

# --- X API Auth ---
client = tweepy.Client(
    bearer_token=os.getenv("X_BEARER_TOKEN"),
    consumer_key=os.getenv("X_API_KEY"),
    consumer_secret=os.getenv("X_API_SECRET"),
    access_token=os.getenv("X_ACCESS_TOKEN"),
    access_token_secret=os.getenv("X_ACCESS_SECRET"),
    wait_on_rate_limit=True
)

# --- Config ---
AGENT_HANDLE = "cybergod"
TWITTER_HANDLE = "@BC_Research_"
REPO = "cybergod-duck/agenton-ops"
GITHUB_API = "https://api.github.com"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
MONITOR_ACCOUNTS = ["AgentOnHQ", "FluxANetwork"]  # update with real handles
MONITOR_KEYWORDS = ["quest", "bounty", "reward", "earn", "USDC", "new task", "cybergod"]
POLL_INTERVAL_SECONDS = 300  # check every 5 minutes
SEEN_IDS_FILE = ".seen_tweet_ids"


# ============================================================
# FUNCTION 1: POST QUEST COMPLETION
# ============================================================

def build_quest_post(quest_name: str, reward: str, platform: str = "AgentOn") -> str:
    """Build a post announcing a completed quest."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    post = (
        f"\u2705 Quest complete.\n\n"
        f"Agent: {AGENT_HANDLE} (Level 2 Sparked)\n"
        f"Quest: {quest_name}\n"
        f"Reward: {reward}\n"
        f"Platform: {platform}\n"
        f"Date: {timestamp}\n\n"
        f"Building on Base. Earning daily. \U0001f9e0\n"
        f"#AgentOn #Base #USDC #AI"
    )
    return post[:280]  # hard cap at X limit


def post_quest_completion(quest_name: str, reward: str, platform: str = "AgentOn"):
    """Post a quest completion tweet from @BC_Research_."""
    text = build_quest_post(quest_name, reward, platform)
    try:
        response = client.create_tweet(text=text)
        tweet_id = response.data["id"]
        print(f"[POST] Tweeted quest completion: {tweet_id}")
        log_to_repo(f"[{datetime.now().isoformat()}] Posted quest completion: {quest_name} | Tweet ID: {tweet_id}\n")
        return tweet_id
    except tweepy.TweepyException as e:
        print(f"[POST ERROR] {e}")
        return None


# ============================================================
# FUNCTION 2: MONITOR FOR NEW QUESTS
# ============================================================

def load_seen_ids() -> set:
    if os.path.exists(SEEN_IDS_FILE):
        with open(SEEN_IDS_FILE, "r") as f:
            return set(line.strip() for line in f if line.strip())
    return set()


def save_seen_id(tweet_id: str):
    with open(SEEN_IDS_FILE, "a") as f:
        f.write(f"{tweet_id}\n")


def is_quest_tweet(text: str) -> bool:
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in MONITOR_KEYWORDS)


def format_quest_entry(tweet_text: str, author: str, tweet_id: str) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return (
        f"\n### \U0001f6a8 URGENT — New Quest Signal from @{author}\n"
        f"- **Platform:** AgentOn (unverified — check quest board)\n"
        f"- **Reward:** Unknown — check tweet\n"
        f"- **Deadline:** Rolling\n"
        f"- **Effort:** Unknown\n"
        f"- **Task:** {tweet_text[:200]}\n"
        f"- **Source:** https://x.com/{author}/status/{tweet_id}\n"
        f"- **Detected:** {today}\n"
        f"- **Status:** Open\n"
    )


def append_to_active_quests(entry: str):
    """Append a new quest entry to quests/active.md via GitHub API."""
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    # Get current file
    r = requests.get(f"{GITHUB_API}/repos/{REPO}/contents/quests/active.md", headers=headers)
    if r.status_code != 200:
        print(f"[GITHUB ERROR] Could not fetch active.md: {r.status_code}")
        return
    data = r.json()
    import base64
    current_content = base64.b64decode(data["content"]).decode("utf-8")
    sha = data["sha"]
    new_content = current_content + entry
    encoded = base64.b64encode(new_content.encode("utf-8")).decode("utf-8")
    payload = {
        "message": f"bot: new quest signal detected from Twitter",
        "content": encoded,
        "sha": sha
    }
    update = requests.put(
        f"{GITHUB_API}/repos/{REPO}/contents/quests/active.md",
        headers=headers,
        json=payload
    )
    if update.status_code in (200, 201):
        print(f"[GITHUB] Updated quests/active.md with new quest signal")
    else:
        print(f"[GITHUB ERROR] Failed to update active.md: {update.status_code} {update.text}")


def monitor_accounts():
    """Search recent tweets from monitored accounts for quest signals."""
    seen = load_seen_ids()
    for account in MONITOR_ACCOUNTS:
        try:
            # Search recent tweets from this account
            query = f"from:{account} ({' OR '.join(MONITOR_KEYWORDS)})"
            tweets = client.search_recent_tweets(
                query=query,
                max_results=10,
                tweet_fields=["id", "text", "author_id", "created_at"]
            )
            if not tweets.data:
                continue
            for tweet in tweets.data:
                tid = str(tweet.id)
                if tid in seen:
                    continue
                if is_quest_tweet(tweet.text):
                    print(f"[MONITOR] New quest signal from @{account}: {tweet.text[:80]}...")
                    entry = format_quest_entry(tweet.text, account, tid)
                    append_to_active_quests(entry)
                    save_seen_id(tid)
        except tweepy.TweepyException as e:
            print(f"[MONITOR ERROR] @{account}: {e}")


def log_to_repo(message: str):
    """Append a line to outputs/twitter-agent-log.md via GitHub API."""
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    import base64
    path = "outputs/twitter-agent-log.md"
    r = requests.get(f"{GITHUB_API}/repos/{REPO}/contents/{path}", headers=headers)
    if r.status_code == 200:
        data = r.json()
        current = base64.b64decode(data["content"]).decode("utf-8")
        sha = data["sha"]
    else:
        current = "# Twitter Agent Log\n\n"
        sha = None
    new_content = current + message
    encoded = base64.b64encode(new_content.encode()).decode()
    payload = {"message": "bot: twitter agent log update", "content": encoded}
    if sha:
        payload["sha"] = sha
    requests.put(f"{GITHUB_API}/repos/{REPO}/contents/{path}", headers=headers, json=payload)


# ============================================================
# MAIN LOOP
# ============================================================

def run():
    print(f"[cybergod Twitter Agent] Starting. Monitoring: {MONITOR_ACCOUNTS}")
    print(f"[cybergod Twitter Agent] Poll interval: {POLL_INTERVAL_SECONDS}s")
    while True:
        print(f"\n[{datetime.now().isoformat()}] Running monitor cycle...")
        monitor_accounts()
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        if sys.argv[1] == "post":
            # Manual post: python cybergod_twitter_agent.py post "Quest Name" "5 USDC"
            quest = sys.argv[2] if len(sys.argv) > 2 else "Unknown Quest"
            reward = sys.argv[3] if len(sys.argv) > 3 else "? USDC"
            post_quest_completion(quest, reward)
        elif sys.argv[1] == "monitor":
            run()
    else:
        # Default: run full loop
        run()
