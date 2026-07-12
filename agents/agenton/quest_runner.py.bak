#!/usr/bin/env python3
"""
quest_runner.py — Autonomous quest executor for AgentOn
======================================================
Parses quests/active.md and generates solutions for non-automatable quests.
Saves deliverables under outputs/agenton/<quest_id>/submission.md.
"""

import os
import sys
import re
import time
import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
import requests

# ── Paths & Config ────────────────────────────────────────────────────────────
ROOT_DIR       = Path(r"C:\BC RESEARCH\AI_FACTORY")
AGENTON_DIR    = ROOT_DIR / "AgentOn"
BOT_ENV_PATH   = ROOT_DIR / "bot.env"
ACTIVE_MD_PATH = AGENTON_DIR / "quests" / "active.md"
OUTPUTS_DIR    = AGENTON_DIR / "outputs" / "agenton"
LOG_FILE       = OUTPUTS_DIR / "quest_runner.log"

LLM_MODEL      = "google/gemini-2.5-flash"
LLM_API_BASE   = "https://openrouter.ai/api/v1"

# ── Logging Setup ─────────────────────────────────────────────────────────────
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8")
    ],
)
log = logging.getLogger("quest_runner")
sys.stdout.reconfigure(encoding="utf-8")

# ── Env Loading ───────────────────────────────────────────────────────────────
def load_env(path: Path = BOT_ENV_PATH) -> dict:
    env = {}
    if not path.exists():
        log.warning(f"bot.env not found at {path}")
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env

# ── Parser for quests/active.md ───────────────────────────────────────────────
def parse_quests(active_md_path: Path) -> list[dict]:
    quests = []
    if not active_md_path.exists():
        log.warning(f"quests/active.md not found at {active_md_path}")
        return quests
    
    content = active_md_path.read_text(encoding="utf-8")
    blocks = content.split("## Quest:")
    
    for block in blocks[1:]:
        lines = block.splitlines()
        if not lines:
            continue
        
        title = lines[0].strip()
        quest = {
            "title": title,
            "platform": "",
            "quest_id": "",
            "reward": "",
            "category": "",
            "difficulty": "",
            "timebox": "",
            "input_files": "",
            "output_target": "",
            "status": "",
            "requirements": [],
            "acceptance_criteria": []
        }
        
        current_section = None
        for line in lines[1:]:
            line_str = line.strip()
            if not line_str:
                continue
            
            if line_str.startswith("- Platform:"):
                quest["platform"] = line_str.split("Platform:", 1)[1].strip()
            elif line_str.startswith("- Quest ID:"):
                quest["quest_id"] = line_str.split("Quest ID:", 1)[1].strip().replace("`", "")
            elif line_str.startswith("- Reward:"):
                quest["reward"] = line_str.split("Reward:", 1)[1].strip()
            elif line_str.startswith("- Category:"):
                quest["category"] = line_str.split("Category:", 1)[1].strip().lower()
            elif line_str.startswith("- Difficulty:"):
                quest["difficulty"] = line_str.split("Difficulty:", 1)[1].strip()
            elif line_str.startswith("- Timebox:"):
                quest["timebox"] = line_str.split("Timebox:", 1)[1].strip()
            elif line_str.startswith("- Input Files:"):
                quest["input_files"] = line_str.split("Input Files:", 1)[1].strip()
            elif line_str.startswith("- Output Target:"):
                quest["output_target"] = line_str.split("Output Target:", 1)[1].strip()
            elif line_str.startswith("- Status:"):
                quest["status"] = line_str.split("Status:", 1)[1].strip().lower()
            elif line_str.startswith("- Key Requirements:"):
                current_section = "requirements"
            elif line_str.startswith("- Acceptance Criteria:"):
                current_section = "acceptance_criteria"
            elif line_str.startswith("- ") or line_str.startswith("  -"):
                val = line_str.lstrip("- ").strip()
                if current_section == "requirements":
                    quest["requirements"].append(val)
                elif current_section == "acceptance_criteria":
                    quest["acceptance_criteria"].append(val)
            elif line_str.startswith("---"):
                current_section = None
                
        quests.append(quest)
    
    return quests

# ── Read Inputs / Context ──────────────────────────────────────────────────────
def get_quest_inputs(quest_id: str) -> str:
    inputs_dir = AGENTON_DIR / "quests" / quest_id / "inputs"
    if not inputs_dir.exists():
        return "None"
    
    input_texts = []
    for f in inputs_dir.glob("*"):
        if f.is_file():
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
                input_texts.append(f"### Input File: {f.name}\n```\n{content}\n```")
            except Exception as e:
                log.warning(f"Error reading input file {f.name}: {e}")
                
    return "\n\n".join(input_texts) if input_texts else "None"

# ── LLM Templates ─────────────────────────────────────────────────────────────
TEMPLATES = {
    "writing": {
        "system": (
            "You are the cybergod AI_Factory work agent executing tasks from AgentOn. "
            "For this quest: Follow the 'Key Requirements' and 'Acceptance Criteria' exactly. "
            "Keep answers concise, production-ready, and copy-pasteable into the platform. "
            "Avoid speculative claims, focus on verifiable, neutral content. "
            "If information is missing, create reasonable assumptions and list them explicitly in a short "
            "'Assumptions' section at the top. You specialize in clear, professional, and engaging copywriting, "
            "technical documentation, or blog posts."
        ),
        "user": (
            "Quest title: {title}\n"
            "Category: {category} (Writing)\n"
            "Reward: {reward}\n"
            "Difficulty: {difficulty}\n"
            "Timebox: {timebox} minutes\n\n"
            "Key Requirements:\n{requirements}\n\n"
            "Acceptance Criteria:\n{acceptance_criteria}\n\n"
            "Context files / Examples:\n{context_files}\n\n"
            "Task:\n"
            "Produce a single markdown answer ready to paste into AgentOn that:\n"
            "- Satisfies all writing requirements.\n"
            "- Explicitly addresses each acceptance criterion.\n"
            "- Includes a short 'Proof of Work' section describing what was done.\n\n"
            "Return ONLY the answer content, no meta commentary."
        )
    },
    "coding": {
        "system": (
            "You are the cybergod AI_Factory work agent executing tasks from AgentOn. "
            "For this quest: Follow the 'Key Requirements' and 'Acceptance Criteria' exactly. "
            "Keep answers concise, production-ready, and copy-pasteable into the platform. "
            "Avoid speculative claims, focus on verifiable, neutral content. "
            "If information is missing, create reasonable assumptions and list them explicitly in a short "
            "'Assumptions' section at the top. You are an expert software engineer. Provide robust, "
            "well-commented, and clean code snippets, scripts, or fixes."
        ),
        "user": (
            "Quest title: {title}\n"
            "Category: {category} (Coding)\n"
            "Reward: {reward}\n"
            "Difficulty: {difficulty}\n"
            "Timebox: {timebox} minutes\n\n"
            "Key Requirements:\n{requirements}\n\n"
            "Acceptance Criteria:\n{acceptance_criteria}\n\n"
            "Context files / Examples:\n{context_files}\n\n"
            "Task:\n"
            "Produce a single markdown answer ready to paste into AgentOn that:\n"
            "- Satisfies all coding requirements.\n"
            "- Provides clean, copy-pasteable code blocks with explanatory comments.\n"
            "- Explicitly addresses each acceptance criterion.\n"
            "- Includes a short 'Proof of Work' section describing what was done.\n\n"
            "Return ONLY the answer content, no meta commentary."
        )
    },
    "data": {
        "system": (
            "You are the cybergod AI_Factory work agent executing tasks from AgentOn. "
            "For this quest: Follow the 'Key Requirements' and 'Acceptance Criteria' exactly. "
            "Keep answers concise, production-ready, and copy-pasteable into the platform. "
            "Avoid speculative claims, focus on verifiable, neutral content. "
            "If information is missing, create reasonable assumptions and list them explicitly in a short "
            "'Assumptions' section at the top. You are a data analyst. Provide well-structured Markdown tables, "
            "clear summaries, or structured data transformations."
        ),
        "user": (
            "Quest title: {title}\n"
            "Category: {category} (Data/Analysis)\n"
            "Reward: {reward}\n"
            "Difficulty: {difficulty}\n"
            "Timebox: {timebox} minutes\n\n"
            "Key Requirements:\n{requirements}\n\n"
            "Acceptance Criteria:\n{acceptance_criteria}\n\n"
            "Context files / Examples:\n{context_files}\n\n"
            "Task:\n"
            "Produce a single markdown answer ready to paste into AgentOn that:\n"
            "- Satisfies all data requirements.\n"
            "- Formats findings, datasets, or summaries using clean markdown tables and structured points.\n"
            "- Explicitly addresses each acceptance criterion.\n"
            "- Includes a short 'Proof of Work' section describing what was done.\n\n"
            "Return ONLY the answer content, no meta commentary."
        )
    },
    "general": {
        "system": (
            "You are the cybergod AI_Factory work agent executing tasks from AgentOn. "
            "For this quest: Follow the 'Key Requirements' and 'Acceptance Criteria' exactly. "
            "Keep answers concise, production-ready, and copy-pasteable into the platform. "
            "Avoid speculative claims, focus on verifiable, neutral content. "
            "If information is missing, create reasonable assumptions and list them explicitly in a short "
            "'Assumptions' section at the top."
        ),
        "user": (
            "Quest title: {title}\n"
            "Category: {category}\n"
            "Reward: {reward}\n"
            "Difficulty: {difficulty}\n"
            "Timebox: {timebox} minutes\n\n"
            "Key Requirements:\n{requirements}\n\n"
            "Acceptance Criteria:\n{acceptance_criteria}\n\n"
            "Context files / Examples:\n{context_files}\n\n"
            "Task:\n"
            "Produce a single markdown answer ready to paste into AgentOn that:\n"
            "- Satisfies all requirements.\n"
            "- Explicitly addresses each acceptance criterion.\n"
            "- Includes a short 'Proof of Work' section describing what was done.\n\n"
            "Return ONLY the answer content, no meta commentary."
        )
    }
}

# ── OpenRouter Call ───────────────────────────────────────────────────────────
def call_openrouter(system_prompt: str, user_prompt: str, api_key: str) -> str:
    url = f"{LLM_API_BASE}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/BCR-AgentOn",
        "X-Title": "BCR-AgentOn-QuestRunner",
    }
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "max_tokens": 4000,
        "temperature": 0.5,
    }
    
    r = requests.post(url, headers=headers, json=payload, timeout=90)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()

# ── Git Sync ──────────────────────────────────────────────────────────────────
def git_sync(quest_title: str):
    try:
        subprocess.run(["git", "add", "."], cwd=AGENTON_DIR, check=True, capture_output=True)
        res = subprocess.run(["git", "status", "--porcelain"], cwd=AGENTON_DIR, capture_output=True, text=True)
        if res.stdout.strip():
            subprocess.run(["git", "commit", "-m", f"bot: quest deliverable generated for {quest_title}"],
                           cwd=AGENTON_DIR, check=True, capture_output=True)
            subprocess.run(["git", "push"], cwd=AGENTON_DIR, check=True, capture_output=True)
            log.info("Git push completed successfully.")
    except Exception as e:
        log.warning(f"Git sync failed: {e}")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    log.info("Starting AgentOn Quest Runner...")
    
    env = load_env()
    api_key = env.get("OPENROUTER_API_KEY")
    if not api_key:
        log.error("OPENROUTER_API_KEY not found in bot.env")
        sys.exit(1)
        
    quests = parse_quests(ACTIVE_MD_PATH)
    open_agenton_quests = [q for q in quests if q["platform"].lower() == "agenton" and q["status"] == "open"]
    
    log.info(f"Found {len(open_agenton_quests)} open AgentOn quests in active.md")
    
    generated_count = 0
    for q in open_agenton_quests:
        qid = q["quest_id"]
        title = q["title"]
        cat = q["category"]
        
        # Determine preset template category
        preset_cat = "general"
        if cat in ["writing", "social", "content"]:
            preset_cat = "writing"
        elif cat in ["coding", "code"]:
            preset_cat = "coding"
        elif cat in ["data", "research", "analysis"]:
            preset_cat = "data"
            
        target_dir = OUTPUTS_DIR / qid
        target_file = target_dir / "submission.md"
        
        # Check if already completed/generated
        if target_file.exists():
            log.info(f"Skipping Quest {qid} ({title}) — deliverable already exists.")
            continue
            
        log.info(f"Processing Quest {qid} ({title}) in category '{cat}' (using '{preset_cat}' template)...")
        
        reqs_str = "\n".join(f"- {r}" for r in q["requirements"])
        accs_str = "\n".join(f"- {a}" for a in q["acceptance_criteria"])
        
        # Load inputs / examples if any
        context_files = get_quest_inputs(qid)
        
        templates = TEMPLATES[preset_cat]
        system_prompt = templates["system"]
        user_prompt = templates["user"].format(
            title=title,
            category=cat,
            reward=q["reward"],
            difficulty=q["difficulty"],
            timebox=q["timebox"],
            requirements=reqs_str,
            acceptance_criteria=accs_str,
            context_files=context_files
        )
        
        try:
            log.info("Calling OpenRouter LLM...")
            deliverable = call_openrouter(system_prompt, user_prompt, api_key)
            
            target_dir.mkdir(parents=True, exist_ok=True)
            target_file.write_text(deliverable, encoding="utf-8")
            log.info(f"✅ Deliverable saved to {target_file}")
            
            generated_count += 1
            git_sync(title)
            
            # Stagger LLM requests
            time.sleep(5)
            
        except Exception as e:
            log.error(f"Error processing Quest {qid}: {e}")
            
    log.info(f"Quest Runner finished. Generated {generated_count} deliverables.")

if __name__ == "__main__":
    main()
