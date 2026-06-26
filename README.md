# agenton-ops

> **Mission:** Maximize daily USDC earned via AgentOn quests using the cybergod + Google Antigravity earn loop.

## Stack
| Tool | Role |
|---|---|
| **Perplexity (AgentON Ops Space)** | Research, orchestration, prompt design |
| **Google Antigravity** | Quest execution, content generation, output delivery |
| **Grok CLI** | Code tasks, scraping, automation |
| **AgentOn** | Quest platform (agent: `cybergod`, Level 2 Sparked) |
| **Base Wallet** | On-chain USDC receipt |
| **FluxA** | Agent ID binding, additional rewards |

## Rules of Engagement
1. **Perplexity** writes quests into `quests/active.md` and prompts into `prompts/antigravity/`.
2. **Antigravity** reads `quests/active.md`, executes, and drops outputs in `outputs/`.
3. Completed quests move to `quests/completed/`.
4. All confirmed payouts are logged in `wallet/payout-tracker.md`.
5. Run `playbooks/daily-loop.md` every morning — no exceptions.

## Quick Start for Antigravity
Paste this into Antigravity at the start of every session:

```
Read the repo at https://github.com/cybergod-duck/agenton-ops.
Check quests/active.md for open tasks.
Execute each task completely.
Save all outputs to the outputs/ folder.
Update wallet/payout-tracker.md with any confirmed rewards.
Report blockers in outputs/blockers.md.
```

## Folder Map
```
AgentOn/
├── README.md
├── quests/
│   ├── active.md           ← Antigravity reads this
│   └── completed/
├── prompts/
│   ├── antigravity/        ← paste-ready prompts for Antigravity
│   └── grok/               ← Grok CLI prompts
├── playbooks/
│   └── daily-loop.md       ← your morning routine
├── outputs/
│   └── .gitkeep
└── wallet/
    └── payout-tracker.md
```
