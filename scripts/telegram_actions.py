"""
telegram_actions.py — Telethon user-account automation for AgentOn earn loop.

Provides:
    run_tg_join(group)           — join a public Telegram group/channel
    run_tg_send(group, text)     — send a message to a joined group
    run_tg_join_and_send(group, text) — join then send (with human-paced delay)

Uses the existing StringSession from AI.env and api_id/api_hash from bot.env.
All actions are rate-limited: max 5 TG API calls per earn_loop run, and
cached joins in agents/telegram/joined-groups.txt to avoid re-joining.

Usage from earn_loop.py:
    from telegram_actions import TelegramActions
    tg = TelegramActions(keys, cache_path, log_path)
    ok, msg = await tg.join_and_send("groupusername", "Hello from agent!")
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
from datetime import datetime
from pathlib import Path


def _load_env(bot_env_path: str) -> dict:
    env = {}
    if os.path.exists(bot_env_path):
        with open(bot_env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    return env


def _load_string_session(ai_env_path: str) -> str | None:
    if not os.path.exists(ai_env_path):
        return None
    try:
        data = json.loads(Path(ai_env_path).read_text(encoding="utf-8"))
        for entry in data:
            if isinstance(entry, dict) and entry.get("envVar") == "TELETHON_STRING_SESSION":
                return entry["value"]
    except Exception:
        pass
    return None


def _load_joined_groups(cache_path: str) -> set:
    joined = set()
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    joined.add(line.lower().lstrip("@"))
    return joined


def _save_joined_group(cache_path: str, group: str) -> None:
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "a", encoding="utf-8") as f:
        f.write(f"\n{group.lower().lstrip('@')}")


def _log_tg_action(log_path: str, action: str, target: str, result: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = f"| {timestamp} | {action} | {target} | {result} |\n"
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    if not os.path.exists(log_path) or os.path.getsize(log_path) == 0:
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("# Telegram Actions Log\n\n| Timestamp | Action | Target | Result |\n|---|---|---|---|\n")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(row)


def extract_tg_username(text: str) -> str | None:
    """Extract a Telegram username or group handle from a URL or @mention."""
    text = text.strip()
    # t.me/username or t.me/+invite_hash
    m = re.search(r't\.me/([A-Za-z0-9_]{3,})', text)
    if m:
        return m.group(1)
    # @username
    m = re.search(r'@([A-Za-z0-9_]{3,})', text)
    if m:
        return m.group(1)
    # bare alphanumeric username
    if re.fullmatch(r'[A-Za-z0-9_]{3,}', text):
        return text
    return None


class TelegramActions:
    """Thin Telethon wrapper for AgentOn earn loop Telegram tasks."""

    MAX_LOOP_CALLS = 5

    def __init__(
        self,
        keys: dict,
        join_cache_path: str,
        tg_log_path: str,
        ai_env_path: str = r"C:\BC RESEARCH\AI_FACTORY\AI.env",
    ):
        self.keys = keys
        self.join_cache_path = join_cache_path
        self.tg_log_path = tg_log_path
        self.ai_env_path = ai_env_path
        self._loop_calls = 0
        self._client = None

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    def _check_loop_limit(self) -> bool:
        return self._loop_calls < self.MAX_LOOP_CALLS

    async def _get_client(self):
        if self._client and self._client.is_connected():
            return self._client
        try:
            from telethon import TelegramClient
            from telethon.sessions import StringSession
        except ImportError:
            raise RuntimeError("telethon not installed — run: pip install telethon")

        ss = _load_string_session(self.ai_env_path)
        if not ss:
            raise RuntimeError("TELETHON_STRING_SESSION not found in AI.env")

        api_id = int(self.keys.get("TRTH_CHANNEL_TELEGRAM_API_ID", 0))
        api_hash = self.keys.get("TRTH_CHANNEL_TELEGRAM_API_HASH", "")
        if not api_id or not api_hash:
            raise RuntimeError("TRTH_CHANNEL_TELEGRAM_API_ID / _HASH not found in bot.env")

        self._client = TelegramClient(StringSession(ss), api_id, api_hash)
        await self._client.connect()
        if not await self._client.is_user_authorized():
            raise RuntimeError("Telethon session expired — re-run telethon_login.py")
        return self._client

    async def disconnect(self):
        if self._client and self._client.is_connected():
            await self._client.disconnect()
            self._client = None

    # ------------------------------------------------------------------ #
    #  Public actions — each returns (ok: bool, message: str)
    # ------------------------------------------------------------------ #

    async def join_group(self, group: str) -> tuple[bool, str]:
        """Join a public Telegram group or channel."""
        group = group.lower().lstrip("@")
        if not self._check_loop_limit():
            return False, "TG loop call limit (5) reached"

        joined = _load_joined_groups(self.join_cache_path)
        if group in joined:
            print(f"[TG] Already joined @{group} (cached). Skipping.")
            _log_tg_action(self.tg_log_path, "join", group, "cached — skipped")
            return True, f"Already joined @{group} (cached)"

        try:
            from telethon.tl.functions.channels import JoinChannelRequest
            from telethon.errors import UserAlreadyParticipantError, FloodWaitError
        except ImportError:
            raise RuntimeError("telethon not installed")

        self._loop_calls += 1
        client = await self._get_client()
        try:
            # Random human-paced delay
            await asyncio.sleep(random.randint(3, 8))
            entity = await client.get_entity(group)
            await client(JoinChannelRequest(entity))
            _save_joined_group(self.join_cache_path, group)
            _log_tg_action(self.tg_log_path, "join", group, "joined")
            print(f"[TG] Joined @{group}")
            return True, f"Joined @{group}"
        except UserAlreadyParticipantError:
            _save_joined_group(self.join_cache_path, group)
            _log_tg_action(self.tg_log_path, "join", group, "already member")
            return True, f"Already member of @{group}"
        except FloodWaitError as e:
            _log_tg_action(self.tg_log_path, "join", group, f"flood wait {e.seconds}s")
            return False, f"Telegram rate limit: wait {e.seconds}s"
        except Exception as e:
            _log_tg_action(self.tg_log_path, "join", group, f"error: {str(e)[:80]}")
            return False, f"Join failed for @{group}: {e}"

    async def send_message(self, group: str, text: str) -> tuple[bool, str]:
        """Send a message to a Telegram group/channel."""
        group = group.lower().lstrip("@")
        if not self._check_loop_limit():
            return False, "TG loop call limit (5) reached"

        self._loop_calls += 1
        client = await self._get_client()
        try:
            await asyncio.sleep(random.randint(4, 10))
            entity = await client.get_entity(group)
            msg = await client.send_message(entity, text)
            link = f"https://t.me/{group}/{msg.id}"
            _log_tg_action(self.tg_log_path, "send", group, f"msg_id={msg.id}")
            print(f"[TG] Sent message to @{group} (msg_id={msg.id})")
            return True, link
        except Exception as e:
            _log_tg_action(self.tg_log_path, "send", group, f"error: {str(e)[:80]}")
            return False, f"Send failed to @{group}: {e}"

    async def join_and_send(self, group: str, text: str) -> tuple[bool, str]:
        """Join group then send a message. Returns (ok, proof_link)."""
        ok, msg = await self.join_group(group)
        if not ok:
            return False, msg
        # Brief lurk delay after joining before sending
        await asyncio.sleep(random.randint(5, 12))
        ok2, msg2 = await self.send_message(group, text)
        return ok2, msg2

    async def signup_bot(self, bot_username: str, start_cmd: str = "/start") -> tuple[bool, str]:
        """Send /start to a bot (for quest signup via bot)."""
        bot_username = bot_username.lower().lstrip("@")
        if not self._check_loop_limit():
            return False, "TG loop call limit (5) reached"

        self._loop_calls += 1
        client = await self._get_client()
        try:
            await asyncio.sleep(random.randint(2, 6))
            entity = await client.get_entity(bot_username)
            await client.send_message(entity, start_cmd)
            _log_tg_action(self.tg_log_path, "bot_start", bot_username, f"sent {start_cmd}")
            print(f"[TG] Sent {start_cmd} to @{bot_username}")
            return True, f"Sent {start_cmd} to @{bot_username}"
        except Exception as e:
            _log_tg_action(self.tg_log_path, "bot_start", bot_username, f"error: {str(e)[:80]}")
            return False, f"Bot start failed @{bot_username}: {e}"
