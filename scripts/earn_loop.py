import os
import re
import sys
import json
import time
import requests
import subprocess
import tweepy
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')

loop_x_api_calls = 0

def clean_json_text(text):
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()

def get_today_api_call_count(log_path):
    if not os.path.exists(log_path):
        return 0
    today_str = datetime.now().strftime("%Y-%m-%d")
    count = 0
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith(f"| {today_str}"):
                    if "$0.01" in line:
                        count += 1
    except Exception as e:
        print(f"Error reading usage log: {e}")
    return count

def log_twitter_api_call(log_path, endpoint, target, notes=""):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = f"| {timestamp} | {endpoint} | {target} | $0.01 | {notes} |\n"
    try:
        if not os.path.exists(log_path) or os.path.getsize(log_path) == 0:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("# Twitter API Usage Log\n\n| Timestamp | Endpoint | Target | Estimated Cost | Notes |\n|---|---|---|---|---|\n")
        
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(row)
    except Exception as e:
        print(f"Failed to log Twitter API call: {e}")

def x_api_call(client, log_path, func, endpoint, target, *args, **kwargs):
    global loop_x_api_calls
    
    # Check daily limit
    today_calls = get_today_api_call_count(log_path)
    if today_calls >= 50:
        raise Exception("Daily Twitter API call limit (50) reached.")
    
    # Check loop limit
    if loop_x_api_calls >= 10:
        raise Exception("Loop Twitter API call limit (10) reached.")
    
    loop_x_api_calls += 1
    print(f"Executing Twitter API call: {endpoint} on target: {target} (Loop call: {loop_x_api_calls})")
    try:
        res = func(*args, **kwargs)
        log_twitter_api_call(log_path, endpoint, target, "Success")
        return res
    except Exception as e:
        log_twitter_api_call(log_path, endpoint, target, f"Failed: {str(e)[:100]}")
        raise e

def load_followed_accounts(cache_path):
    if not os.path.exists(cache_path):
        return set()
    follows = set()
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    follows.add(line.lower())
    except Exception as e:
        print(f"Error reading follow cache: {e}")
    return follows

def save_followed_account(cache_path, username):
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "a", encoding="utf-8") as f:
            f.write(f"\n{username}")
    except Exception as e:
        print(f"Error saving to follow cache: {e}")

def run_follow_action(client, log_path, cache_path, username):
    username = username.strip().replace("@", "")
    cached = load_followed_accounts(cache_path)
    if username.lower() in cached:
        print(f"Username @{username} is already in follow cache. Skipping follow call.")
        return True, "Already followed (cached)"
    
    # 1. Get user ID
    try:
        user_resp = x_api_call(
            client, log_path,
            client.get_user,
            "GET /2/users/by/username/:username",
            username,
            username=username
        )
        if not user_resp or not user_resp.data:
            return False, f"Could not find user @{username}"
        user_id = user_resp.data.id
    except Exception as e:
        return False, f"Failed to get user ID: {e}"
        
    # 2. Follow user
    try:
        follow_resp = x_api_call(
            client, log_path,
            client.follow_user,
            "POST /2/users/:id/following",
            str(user_id),
            target_user_id=user_id
        )
        success = False
        if follow_resp and follow_resp.data:
            success = follow_resp.data.get("following") or follow_resp.data.get("pending_follow")
        
        if success:
            save_followed_account(cache_path, username)
            return True, "Follow successful"
        else:
            return False, f"Follow response: {follow_resp}"
    except Exception as e:
        return False, f"Failed to follow: {e}"

def run_tweet_action(client, log_path, action_type, text, target_tweet_url=None):
    full_text = text
    if target_tweet_url:
        match = re.search(r'https?://(?:x|twitter)\.com/([A-Za-z0-9_]+)/status/', target_tweet_url, re.IGNORECASE)
        handle = match.group(1) if match else None
        if handle:
            mention = f"@{handle}"
            if mention not in full_text:
                full_text = f"{mention} {full_text}"
        if target_tweet_url not in full_text:
            full_text = f"{full_text}\n\n{target_tweet_url}"
            
    if len(full_text) > 280:
        print(f"Warning: Tweet text too long ({len(full_text)} chars). Truncating to 280.")
        full_text = full_text[:277] + "..."

    try:
        resp = x_api_call(
            client, log_path,
            client.create_tweet,
            "POST /2/tweets",
            full_text[:50],
            text=full_text
        )
        if resp and resp.data:
            tweet_id = resp.data["id"]
            tweet_url = f"https://x.com/BC_Research_/status/{tweet_id}"
            return True, tweet_url
        else:
            return False, "Empty response from create_tweet"
    except Exception as e:
        return False, f"Failed to post tweet: {e}"

def run_like_action(client, log_path, target_tweet_url_or_id):
    tweet_id = target_tweet_url_or_id
    if "status/" in str(target_tweet_url_or_id):
        match = re.search(r'status/(\d+)', str(target_tweet_url_or_id))
        if match:
            tweet_id = int(match.group(1))
    else:
        try:
            tweet_id = int(tweet_id)
        except ValueError:
            pass
            
    try:
        resp = x_api_call(
            client, log_path,
            client.like,
            "POST /2/users/:id/likes",
            str(tweet_id),
            tweet_id=tweet_id
        )
        success = False
        if resp and resp.data:
            success = resp.data.get("liked")
        if success:
            return True, "Like successful"
        else:
            return False, f"Like response: {resp}"
    except Exception as e:
        return False, f"Failed to like: {e}"

def run_retweet_action(client, log_path, target_tweet_url_or_id):
    tweet_id = target_tweet_url_or_id
    if "status/" in str(target_tweet_url_or_id):
        match = re.search(r'status/(\d+)', str(target_tweet_url_or_id))
        if match:
            tweet_id = int(match.group(1))
    else:
        try:
            tweet_id = int(tweet_id)
        except ValueError:
            pass
            
    try:
        resp = x_api_call(
            client, log_path,
            client.retweet,
            "POST /2/users/:id/retweets",
            str(tweet_id),
            tweet_id=tweet_id
        )
        success = False
        if resp and resp.data:
            success = resp.data.get("retweeted")
        if success:
            return True, "Retweet successful"
        else:
            return False, f"Retweet response: {resp}"
    except Exception as e:
        return False, f"Failed to retweet: {e}"

def call_llm_for_quest(title, description, goal, keys):
    google_key = keys.get("GOOGLE_API_KEY")
    or_key = keys.get("OPENROUTER_API_KEY")
    
    prompt = f"""You are an AI assistant. Analyze this quest from the AgentOn quest board and classify it for automation.

Title: {title}
Description: {description}
Goal: {goal}

Analyze the requirements and output a JSON object with the following fields:
1. "is_automatable": boolean. True if the quest only requires Twitter (X) actions like following accounts, posting tweets, replying, liking, retweeting, or quoting. Set to False if it requires WeChat, Telegram (joining or chatting, unless it's just a simple link click we can pretend to do), Discord, registering on a website, KYC, trading, or mobile app installation.
2. "required_actions": list of actions to take in order. Each action must be a JSON object with:
   - "type": "follow", "target": "username" (without @, e.g. "Toco_Toco_Toco")
   - "type": "post", "text": "tweet text to post"
   - "type": "like", "target_tweet_id": "numeric tweet ID or full URL"
   - "type": "reply", "target_tweet_id": "numeric tweet ID or full URL", "text": "reply text"
   - "type": "retweet", "target_tweet_id": "numeric tweet ID or full URL"
   - "type": "quote", "target_tweet_id": "numeric tweet ID or full URL", "text": "quote text"
3. "content_summary": string summarizing what was done.
4. "attachments": list of strings (proof links or placeholders). Use "<TWEET_URL>" if a posted/replied/quoted tweet URL should be used, or the profile link "https://x.com/BC_Research_" if follow only.

Guidelines for generating tweet text:
- Make the text sound natural, original, and positive, and strictly follow any instructions in the quest description (e.g. hashtags, handles to mention).
- Keep it under 280 characters.
- Do not use placeholders like [insert link] in the text; instead, output final text.

Format the output strictly as JSON. No markdown formatting around the JSON (no ```json ... ``` blocks). Just the raw JSON object.
"""

    if google_key and not google_key.startswith("AQ."):
        print("Calling native Google Gemini API...")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={google_key}"
        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"}
        }
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=30)
            if r.status_code == 200:
                text = r.json()['candidates'][0]['content']['parts'][0]['text']
                return json.loads(clean_json_text(text))
        except Exception as e:
            print(f"Native Gemini API request failed: {e}")

    if or_key:
        print("Calling OpenRouter (gemini-2.5-flash) fallback...")
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {or_key}"
        }
        payload = {
            "model": "google/gemini-2.5-flash",
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"}
        }
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=30)
            if r.status_code == 200:
                text = r.json()['choices'][0]['message']['content']
                return json.loads(clean_json_text(text))
        except Exception as e:
            print(f"OpenRouter request failed: {e}")
            
    raise Exception("No available LLM API key could successfully process the quest.")

def remove_quest_from_active_file(active_path, quest_id):
    if not os.path.exists(active_path):
        return
    with open(active_path, "r", encoding="utf-8") as f:
        content = f.read()
    pattern = re.compile(
        rf"### [^\n]*\n(?:-[^\n]*\n)*.*?(?:Quest ID: `{quest_id}`).*?\n(?=###|---|##|$)",
        re.DOTALL | re.IGNORECASE
    )
    new_content, count = pattern.subn("", content)
    if count > 0:
        new_content = re.sub(r'\n{3,}', '\n\n', new_content)
        with open(active_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        print(f"Removed quest {quest_id} from active.md.")

def add_quest_to_active_file(active_path, q_full):
    quest_id = q_full.get("id")
    uuid_pattern = re.compile(re.escape(quest_id), re.IGNORECASE)
    if os.path.exists(active_path):
        with open(active_path, "r", encoding="utf-8") as f:
            content = f.read()
        if uuid_pattern.search(content):
            print(f"Quest {quest_id} is already listed in active.md.")
            return False
            
    title = q_full.get("title", "Unknown Quest")
    reward = q_full.get("per_submission_reward")
    reward_str = f"{reward:.2f} USDC" if reward is not None else "0.00 USDC"
    deadline = q_full.get("deadline", "")
    if deadline:
        deadline = deadline.split("T")[0]
    desc = q_full.get("description", "No description provided.")
    goal = q_full.get("goal", "Submit proof.")
    
    desc_clean = desc.replace("\n", " ").strip()
    if len(desc_clean) > 300:
        desc_clean = desc_clean[:297] + "..."
        
    block = (
        f"\n### {title}\n"
        f"- **Platform:** AgentOn\n"
        f"- **Reward:** {reward_str}\n"
        f"- **Deadline:** {deadline}\n"
        f"- **Effort:** Low\n"
        f"- **Task:** {desc_clean}\n"
        f"- **Deliverable:** {goal} (Quest ID: `{quest_id}`)\n"
        f"- **Status:** Open\n"
    )
    
    if os.path.exists(active_path):
        with open(active_path, "r", encoding="utf-8") as f:
            content = f.read()
        insert_marker = "## 🔴 Open Quests"
        if insert_marker in content:
            parts = content.split(insert_marker, 1)
            updated_content = parts[0] + insert_marker + "\n" + block + parts[1]
        else:
            updated_content = content + "\n" + block
    else:
        updated_content = "# Active Quests\n\n## 🔴 Open Quests\n" + block
        
    with open(active_path, "w", encoding="utf-8") as f:
        f.write(updated_content)
    print(f"Added quest {quest_id} to active.md.")
    return True

def write_completed_quest_file(completed_dir, quest_id, title, reward, deadline, desc, goal, submission_id):
    today = datetime.now().strftime("%Y-%m-%d")
    clean_title = re.sub(r'[^a-zA-Z0-9]', '-', title.lower())
    clean_title = re.sub(r'-+', '-', clean_title).strip('-')
    filename = f"{today}-{clean_title[:30]}.md"
    filepath = os.path.join(completed_dir, filename)
    
    content = f"""### {title}
- **Platform:** AgentOn
- **Reward:** {reward}
- **Deadline:** {deadline}
- **Effort:** Low
- **Task:** {desc}
- **Deliverable:** {goal} (Quest ID: `{quest_id}`)
- **Status:** Submitted (awaiting payout)
- **Submission ID:** `{submission_id}`
- **Completed Date:** {today}
"""
    try:
        os.makedirs(completed_dir, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"Created completed quest record: {filepath}")
    except Exception as e:
        print(f"Failed to write completed quest file: {e}")

def log_submission_to_file(log_path, quest_name, quest_id, submission_id, payout, notes):
    today = datetime.now().strftime("%Y-%m-%d")
    payout_str = f"{payout:.2f}" if isinstance(payout, (int, float)) else str(payout)
    row = f"| {today} | {quest_name} | `{quest_id}` | `{submission_id}` | {payout_str} | Submitted | {notes} |\n"
    try:
        if not os.path.exists(log_path) or os.path.getsize(log_path) == 0:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("# Submissions Log\n\n| Date | Quest Name | Quest ID | Submission ID | Payout (USDC) | Status | Notes / Deliverables |\n|---|---|---|---|---|---|---|\n")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(row)
    except Exception as e:
        print(f"Failed to log submission: {e}")

def log_payout_to_file(tracker_path, source, amount, notes):
    today = datetime.now().strftime("%Y-%m-%d")
    amount_str = f"{amount:.2f}" if isinstance(amount, (int, float)) else str(amount)
    row = f"| {today} | {source} | {amount_str} | Pending | {notes} |\n"
    try:
        if os.path.exists(tracker_path):
            with open(tracker_path, "r", encoding="utf-8") as f:
                content = f.read()
            if "## Totals" in content:
                parts = content.split("## Totals", 1)
                updated = parts[0].rstrip() + "\n" + row + "\n" + "## Totals" + parts[1]
                with open(tracker_path, "w", encoding="utf-8") as f:
                    f.write(updated)
                print("Updated payout-tracker.md successfully.")
            else:
                with open(tracker_path, "a", encoding="utf-8") as f:
                    f.write(row)
    except Exception as e:
        print(f"Failed to log payout: {e}")

def sync_git_repo(repo_dir, commit_msg):
    try:
        subprocess.run(["git", "add", "."], check=True, cwd=repo_dir)
        status_res = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, cwd=repo_dir)
        if status_res.stdout.strip():
            subprocess.run(["git", "commit", "-m", commit_msg], check=True, cwd=repo_dir)
            subprocess.run(["git", "push"], check=True, cwd=repo_dir)
            print(f"Git commit and push completed in {repo_dir}.")
        else:
            print("No changes to commit in git.")
    except Exception as e:
        print(f"Git sync failed in {repo_dir}: {e}")

def main():
    root_dir = r"C:\BC RESEARCH\AI_FACTORY"
    agenton_dir = os.path.join(root_dir, "AgentOn")
    bot_env_path = os.path.join(root_dir, "bot.env")
    
    # Paths to log/files
    follow_cache_path = os.path.join(root_dir, "agents", "twitter", "followed-accounts.txt")
    usage_log_path = os.path.join(agenton_dir, "outputs", "twitter-api-usage.md")
    submissions_log_path = os.path.join(agenton_dir, "outputs", "submissions-log.md")
    payout_tracker_path = os.path.join(agenton_dir, "wallet", "payout-tracker.md")
    active_path = os.path.join(agenton_dir, "quests", "active.md")
    completed_dir = os.path.join(agenton_dir, "quests", "completed")

    keys = {}
    if os.path.exists(bot_env_path):
        with open(bot_env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    parts = line.split('=', 1)
                    if len(parts) == 2:
                        keys[parts[0].strip()] = parts[1].strip()

    api_key = keys.get("AGENTON_API_KEY")
    if not api_key:
        print("Error: AGENTON_API_KEY not found in bot.env")
        sys.exit(1)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    base_url = "https://agenton.me/api"

    print(f"[{datetime.now().isoformat()}] --- Starting AgentOn Earn Loop ---")

    # 1. Daily Check-In
    print("Performing daily check-in...")
    try:
        r = requests.post(f"{base_url}/agents/checkin", headers=headers, json={})
        if r.status_code == 200:
            print("Check-in successful!")
        elif r.status_code == 400 and "Already checked in" in r.text:
            print("Check-in already completed today.")
        else:
            print(f"Check-in returned status {r.status_code}: {r.text}")
    except Exception as e:
        print(f"Check-in request failed: {e}")

    # 2. Collect completed quest IDs from completed/
    completed_ids = set()
    uuid_pattern = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)

    if os.path.exists(completed_dir):
        for root, _, files in os.walk(completed_dir):
            for file in files:
                if file.endswith(".md"):
                    with open(os.path.join(root, file), "r", encoding="utf-8") as f:
                        for match in uuid_pattern.finditer(f.read()):
                            completed_ids.add(match.group(0).lower())

    print(f"Found {len(completed_ids)} completed quest IDs in local records.")

    # 3. Initialize Tweepy client
    client = tweepy.Client(
        bearer_token=keys.get("TWITTER_BEARER_TOKEN"),
        consumer_key=keys.get("TWITTER_CONSUMER_KEY"),
        consumer_secret=keys.get("TWITTER_CONSUMER_SECRET"),
        access_token=keys.get("TWITTER_ACCESS_TOKEN"),
        access_token_secret=keys.get("TWITTER_ACCESS_SECRET")
    )

    attempted_quest_ids = set()

    # 4. Recursive Scraping and Execution Loop
    while True:
        today_calls = get_today_api_call_count(usage_log_path)
        daily_limit_exceeded = (today_calls >= 50)
        if daily_limit_exceeded:
            print(f"Daily Twitter API limit of 50 calls is reached (current calls today: {today_calls}). Skip Twitter quests.")
            log_twitter_api_call(usage_log_path, "Daily Limit Exceeded Check", "N/A", f"Skipping Twitter-required quests (today's calls: {today_calls})")

        print("Fetching active quests feed...")
        try:
            r = requests.get(f"{base_url}/agents/feed", headers=headers)
            if r.status_code != 200:
                print(f"Failed to fetch feed: Status {r.status_code}, response: {r.text}")
                break
            
            feed_data = r.json()
            quests = feed_data.get("quests", [])
            print(f"Feed returned {len(quests)} quests.")
        except Exception as e:
            print(f"Failed to fetch feed: {e}")
            break

        # Filter open quests
        open_quests = [q for q in quests if q.get("status") == "not_submitted" and q.get("id", "").lower() not in completed_ids]
        
        # Filter out ones we've already attempted in this run
        unattempted_quests = [q for q in open_quests if q.get("id") not in attempted_quest_ids]
        
        if not unattempted_quests:
            print("No more open, unattempted quests found on the feed. Exiting loop.")
            break

        quest = unattempted_quests[0]
        quest_id = quest["id"]
        attempted_quest_ids.add(quest_id)

        print(f"\nProcessing Quest ID: {quest_id} - '{quest.get('title')}'")

        try:
            r_details = requests.get(f"{base_url}/quests/{quest_id}", headers=headers)
            if r_details.status_code != 200:
                print(f"Failed to fetch details for quest {quest_id}: {r_details.status_code}")
                continue
            q_full = r_details.json()
        except Exception as e:
            print(f"Error fetching details for quest {quest_id}: {e}")
            continue

        title = q_full.get("title", "Unknown Quest")
        reward_val = q_full.get("per_submission_reward")
        reward_str = f"{reward_val:.2f} USDC" if reward_val is not None else "0.00 USDC"
        deadline = q_full.get("deadline", "")
        if deadline:
            deadline = deadline.split("T")[0]
        desc = q_full.get("description", "No description.")
        goal = q_full.get("goal", "Submit proof.")

        print("Analyzing quest with LLM...")
        try:
            llm_res = call_llm_for_quest(title, desc, goal, keys)
            print("LLM Classification Result:", json.dumps(llm_res, indent=2))
        except Exception as e:
            print(f"LLM analysis failed for quest {quest_id}: {e}")
            continue

        is_auto = llm_res.get("is_automatable", False)
        if not is_auto:
            print(f"Quest {quest_id} is NOT fully automatable. Adding to active.md if not present.")
            if add_quest_to_active_file(active_path, q_full):
                sync_git_repo(agenton_dir, f"bot: add manual quest {quest_id} to active.md")
            continue

        required_actions = llm_res.get("required_actions", [])
        requires_twitter = any(a.get("type") in ["follow", "post", "reply", "like", "retweet", "quote"] for a in required_actions)

        if requires_twitter and daily_limit_exceeded:
            print(f"Quest {quest_id} requires Twitter actions, but daily limit is exceeded. Skipping execution.")
            if add_quest_to_active_file(active_path, q_full):
                sync_git_repo(agenton_dir, f"bot: add Twitter-limit-deferred quest {quest_id} to active.md")
            continue

        print(f"Executing automatable quest: {title}")
        success = True
        action_tweet_urls = []

        for action in required_actions:
            atype = action.get("type")
            target = action.get("target") or action.get("target_tweet_id")
            text = action.get("text", "")

            if requires_twitter and loop_x_api_calls >= 10:
                print("Loop Twitter API call limit (10) reached. Exiting earn loop script run.")
                sys.exit(0)

            if atype == "follow":
                ok, msg = run_follow_action(client, usage_log_path, follow_cache_path, target)
                print(f"Follow {target}: {msg}")
                if not ok:
                    success = False
                    break
            elif atype in ["post", "reply", "quote"]:
                ok, tweet_url = run_tweet_action(client, usage_log_path, atype, text, target_tweet_url=target)
                print(f"Tweet {atype}: {'Success: ' + tweet_url if ok else 'Failed: ' + tweet_url}")
                if ok:
                    action_tweet_urls.append(tweet_url)
                else:
                    success = False
                    break
            elif atype == "like":
                ok, msg = run_like_action(client, usage_log_path, target)
                print(f"Like {target}: {msg}")
                if not ok:
                    success = False
                    break
            elif atype == "retweet":
                ok, msg = run_retweet_action(client, usage_log_path, target)
                print(f"Retweet {target}: {msg}")
                if not ok:
                    success = False
                    break
            else:
                print(f"Unknown action type: {atype}")
                success = False
                break

        if not success:
            print(f"Failed to execute actions for quest {quest_id}. Adding to active.md for visibility.")
            if add_quest_to_active_file(active_path, q_full):
                sync_git_repo(agenton_dir, f"bot: add execution-failed quest {quest_id} to active.md")
            continue

        resolved_attachments = []
        for attachment in llm_res.get("attachments", []):
            if attachment == "<TWEET_URL>":
                if action_tweet_urls:
                    resolved_attachments.append(action_tweet_urls[0])
            else:
                resolved_attachments.append(attachment)

        for url in action_tweet_urls:
            if url not in resolved_attachments:
                resolved_attachments.append(url)

        if not resolved_attachments and any(a.get("type") == "follow" for a in required_actions):
            resolved_attachments.append("https://x.com/BC_Research_")

        payload = {
            "content": llm_res.get("content_summary", f"Completed {title}"),
            "attachments": resolved_attachments
        }

        print(f"Submitting quest {quest_id} to AgentOn...")
        try:
            submit_url = f"{base_url}/quests/{quest_id}/submit"
            r_submit = requests.post(submit_url, headers=headers, json=payload)
            print(f"Submission status: {r_submit.status_code}")
            print(f"Submission response: {r_submit.text}")

            if r_submit.status_code == 200:
                res_submit = r_submit.json()
                submission_id = res_submit.get("submission_id") or res_submit.get("id") or "N/A"
                print(f"Submission success! Submission ID: {submission_id}")

                completed_ids.add(quest_id.lower())
                write_completed_quest_file(completed_dir, quest_id, title, reward_str, deadline, desc, goal, submission_id)
                remove_quest_from_active_file(active_path, quest_id)
                log_submission_to_file(submissions_log_path, title, quest_id, submission_id, reward_val if reward_val is not None else 0.0, payload["content"])
                log_payout_to_file(payout_tracker_path, f"{title} (Automated)", reward_val if reward_val is not None else 0.0, f"Submission ID: {submission_id}")

                sync_git_repo(agenton_dir, f"bot: automated quest completion {quest_id}")
                
                print("Sleeping 35s to respect submission rate limit...")
                time.sleep(35)
            else:
                print(f"Failed to submit quest {quest_id}: {r_submit.text}")
                if add_quest_to_active_file(active_path, q_full):
                    sync_git_repo(agenton_dir, f"bot: add submission-failed quest {quest_id} to active.md")
        except Exception as e:
            print(f"Error during submission of quest {quest_id}: {e}")
            if add_quest_to_active_file(active_path, q_full):
                sync_git_repo(agenton_dir, f"bot: add submission-error quest {quest_id} to active.md")

    print(f"[{datetime.now().isoformat()}] --- Earn Loop Completed ---")

if __name__ == "__main__":
    main()
