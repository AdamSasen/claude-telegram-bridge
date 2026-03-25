#!/usr/bin/env python3
"""Telegram bot that bridges messages to Claude Code CLI."""

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import yaml
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters,
)
from telegram.request import HTTPXRequest

from bridge import ClaudeBridge

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("claude-telegram-bridge")

# --- Config ---

def load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    cfg = {"telegram": {}, "claude": {}}
    if config_path.exists():
        with open(config_path) as f:
            cfg = yaml.safe_load(f) or cfg
    return cfg


def get_bot_token(cfg: dict) -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or cfg.get("telegram", {}).get("bot_token", "")
    if not token:
        print("Error: Set TELEGRAM_BOT_TOKEN env var or bot_token in config.yaml")
        sys.exit(1)
    return token


def get_allowed_users(cfg: dict) -> set[int]:
    ids = cfg.get("telegram", {}).get("allowed_user_ids", [])
    return set(ids) if ids else set()

# --- Bot ---

class TelegramBridge:

    def __init__(self, cfg: dict):
        self.cfg = cfg
        claude_cfg = cfg.get("claude", {})
        self.bridge = ClaudeBridge(
            model=claude_cfg.get("model", "sonnet"),
            timeout=claude_cfg.get("timeout", 180),
            working_dir=claude_cfg.get("working_dir", "~"),
            permission_mode=claude_cfg.get("permission_mode", "dontAsk"),
            allowed_tools=claude_cfg.get("allowed_tools", [
                "Read", "Edit", "Write", "Glob", "Grep",
                "Bash(cat *)", "Bash(ls *)", "Bash(pwd)",
                "Bash(head *)", "Bash(tail *)", "Bash(wc *)",
            ]),
            max_turns=claude_cfg.get("max_turns", 0),
        )
        self.allowed_users = get_allowed_users(cfg)
        self._claude_mode: set[int] = set()
        self._last_failed_msg: dict[int, str] = {}  # user_id → last message that failed

    def _is_allowed(self, user_id: int) -> bool:
        if not self.allowed_users:
            return True
        return user_id in self.allowed_users

    # -- Commands --

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message:
            return
        uid = update.effective_user.id
        await update.message.reply_text(
            f"Claude Telegram Bridge\n"
            f"Your user ID: {uid}\n\n"
            "Commands:\n"
            "/claude - Connect to Claude Code\n"
            "/local  - Disconnect (stop forwarding)\n"
            "/clear  - Reset Claude session\n"
            "/status - Show current mode & session\n"
            "/accept - Retry blocked action without restrictions"
        )

    async def cmd_claude(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message:
            return
        uid = update.effective_user.id
        if not self._is_allowed(uid):
            return
        self._claude_mode.add(uid)
        await update.message.reply_text(
            "Claude mode ON. Messages are forwarded to Claude Code.\n"
            "/local to disconnect, /clear to reset session."
        )

    async def cmd_local(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message:
            return
        uid = update.effective_user.id
        if not self._is_allowed(uid):
            return
        self._claude_mode.discard(uid)
        await update.message.reply_text("Local mode. Claude disconnected.")

    async def cmd_clear(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message:
            return
        uid = update.effective_user.id
        if not self._is_allowed(uid):
            return
        self.bridge.clear_session()
        await update.message.reply_text("Claude session cleared. Next message starts fresh.")

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message:
            return
        uid = update.effective_user.id
        if not self._is_allowed(uid):
            return
        mode = "Claude Code" if uid in self._claude_mode else "Local (idle)"
        session = ""
        if self.bridge.session_id:
            session = f"\nSession: {self.bridge.session_id[:12]}..."
        perm = f"\nPermissions: {self.bridge.permission_mode}"
        await update.message.reply_text(f"Mode: {mode}{session}{perm}")

    async def cmd_accept(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message:
            return
        uid = update.effective_user.id
        if not self._is_allowed(uid):
            return
        last_msg = self._last_failed_msg.pop(uid, None)
        if not last_msg:
            await update.message.reply_text("Nothing to accept. No blocked action pending.")
            return

        await update.message.reply_text("Retrying with permissions skipped...")
        await update.message.chat.send_action("typing")

        try:
            response = await self.bridge.send(last_msg, force_skip_permissions=True)
            if not response:
                await update.message.reply_text("(empty response from Claude)")
                return
            await self._send_chunked(update, response)
        except subprocess.TimeoutExpired:
            await update.message.reply_text(
                f"Claude timed out ({self.bridge.timeout}s limit)."
            )
        except Exception as e:
            logger.error("Bridge error on /accept: %s", e, exc_info=True)
            await update.message.reply_text(f"Error: {e}")

    # -- Message handling --

    async def on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.text:
            return
        uid = update.effective_user.id
        if not self._is_allowed(uid):
            logger.warning("Unauthorized user: %d", uid)
            return
        if uid not in self._claude_mode:
            return

        text = update.message.text
        await update.message.chat.send_action("typing")

        try:
            response = await self.bridge.send(text)
            if not response:
                await update.message.reply_text("(empty response from Claude)")
                return
            self._last_failed_msg.pop(uid, None)
            await self._send_chunked(update, response)
        except subprocess.TimeoutExpired:
            await update.message.reply_text(
                f"Claude timed out ({self.bridge.timeout}s limit)."
            )
        except Exception as e:
            logger.error("Bridge error: %s", e, exc_info=True)
            self._last_failed_msg[uid] = text
            await update.message.reply_text(
                f"Error: {e}\n\nSend /accept to retry without permission restrictions."
            )

    async def _send_chunked(self, update: Update, text: str):
        """Send response in chunks respecting Telegram's 4096 char limit."""
        max_len = 4000
        for i in range(0, len(text), max_len):
            chunk = text[i:i + max_len]
            try:
                await update.message.reply_text(chunk, parse_mode="Markdown")
            except Exception:
                await update.message.reply_text(chunk)

    # -- Run --

    def run(self):
        token = get_bot_token(self.cfg)
        request = HTTPXRequest(connect_timeout=20.0, read_timeout=20.0)
        app = Application.builder().token(token).request(request).build()

        app.add_handler(CommandHandler("start", self.cmd_start))
        app.add_handler(CommandHandler("claude", self.cmd_claude))
        app.add_handler(CommandHandler("local", self.cmd_local))
        app.add_handler(CommandHandler("clear", self.cmd_clear))
        app.add_handler(CommandHandler("status", self.cmd_status))
        app.add_handler(CommandHandler("accept", self.cmd_accept))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_message))

        logger.info("Bot starting... Send /start in Telegram.")
        app.run_polling(drop_pending_updates=True)


def main():
    cfg = load_config()
    bot = TelegramBridge(cfg)
    bot.run()


if __name__ == "__main__":
    main()
