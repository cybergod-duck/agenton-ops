# cybergod Twitter Agent

Two-function agent for @BC_Research_:

1. **POST** — Announces completed AgentOn quests on X
2. **MONITOR** — Watches AgentOn + FluxA Twitter for new quest signals, auto-writes them to `quests/active.md`

## Setup

### 1. Get X API credentials
- Go to [console.x.com](https://console.x.com)
- Create an app with **Read + Write** permissions
- Generate Access Token + Secret
- Copy all 5 values into `.env`

### 2. Get GitHub token
- Go to [github.com/settings/tokens](https://github.com/settings/tokens)
- Create a token with `repo` scope
- Add to `.env` as `GITHUB_TOKEN`

### 3. Install + run
```bash
cd agents/twitter
pip install -r requirements.txt
cp .env.example .env
# fill in .env with your keys

# Run the monitor loop (runs forever, checks every 5 min)
python cybergod_twitter_agent.py monitor

# Manually post a quest completion
python cybergod_twitter_agent.py post "My Quest Name" "10 USDC"
```

## How it hooks into the earn loop

```
AgentOn quest completed
  → AG writes to outputs/submissions-log.md
  → You run: python cybergod_twitter_agent.py post "Quest Name" "Reward"
  → @BC_Research_ posts completion tweet
  → Builds cybergod reputation → unlocks higher quests

AgentOn/FluxA tweets new quest
  → Monitor detects it within 5 minutes
  → Auto-writes to quests/active.md with 🚨 URGENT flag
  → Perplexity / Antigravity picks it up next session
  → First-mover advantage on new bounties
```

## Update monitored accounts
Edit `MONITOR_ACCOUNTS` in `cybergod_twitter_agent.py` once you confirm the real X handles for AgentOn and FluxA.
