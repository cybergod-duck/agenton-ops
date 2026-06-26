# User Ideas — Captured for Execution

## 🔴 IN PROGRESS: Multi-Platform AgentOn Stack Replication

**Captured**: 2026-06-26 12:54

### Goal
Replicate the AgentOn earn loop across 5 agent-native earning platforms. Run in parallel without disrupting the primary AgentOn 2-hour scheduler.

### Target Platforms (Priority Order)
1. **BountyBook.ai** — https://www.bountybook.ai/ (REST + MCP, USDC on Base, agent-native)
2. **Claw Earn** (aiagentstore.ai) — https://aiagentstore.ai/claw-earn (On-chain USDC escrow)
3. **dealwork.ai** — https://dealwork.ai/ (Hybrid agent/human)
4. **opentask.ai** — https://opentask.ai/ (Agent-to-agent marketplace)
5. **ugig.net** — https://ugig.net/ (AI agent gig marketplace with API)

### Requirements per Platform
- Signup/register agent profile (BC_Research_ / cybergod branding)
- Connect Base USDC wallet or generate API keys
- Create platform-specific worker script
- Add to task scheduler (staggered, every 3-4 hours)
- Full logging + payout tracking
- Manual approval gate for fund-staking or high-value submissions

### Status
- [ ] BountyBook.ai — researching
- [ ] Claw Earn — researching
- [ ] dealwork.ai — researching
- [ ] opentask.ai — researching
- [ ] ugig.net — researching
