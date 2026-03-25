"""Claude Code CLI subprocess wrapper."""

import asyncio
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class ClaudeBridge:
    """Forward messages to Claude Code CLI and return responses."""

    def __init__(
        self,
        model: str = "sonnet",
        timeout: int = 180,
        working_dir: Optional[str] = None,
        permission_mode: str = "dontAsk",
        allowed_tools: Optional[list[str]] = None,
        max_turns: int = 0,
    ):
        self.model = model
        self.timeout = timeout
        self.working_dir = working_dir
        self.permission_mode = permission_mode
        self.allowed_tools = allowed_tools or []
        self.max_turns = max_turns

    _session_id: Optional[str] = None

    async def send(self, text: str, force_skip_permissions: bool = False) -> str:
        """Send message to Claude Code CLI, return response text."""
        cmd = [
            "claude", "-p",
            "--output-format", "json",
            "--model", self.model,
        ]

        if force_skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        elif self.permission_mode in ("acceptEdits", "bypassPermissions", "dontAsk", "plan"):
            cmd.extend(["--permission-mode", self.permission_mode])

        for tool in self.allowed_tools:
            cmd.extend(["--allowedTools", tool])

        if self.max_turns > 0:
            cmd.extend(["--max-turns", str(self.max_turns)])

        if self._session_id:
            cmd.extend(["--resume", self._session_id])

        cmd.append(text)

        cwd = self._resolve_working_dir()
        env = os.environ.copy()
        env.pop("CLAUDECODE", None)

        logger.info("Sending %d chars to Claude (session=%s)", len(text), self._session_id)

        try:
            result = await asyncio.to_thread(
                subprocess.run, cmd,
                capture_output=True, text=True,
                timeout=self.timeout, cwd=cwd, env=env,
            )
        except subprocess.TimeoutExpired:
            logger.warning("Claude timed out after %ds", self.timeout)
            raise

        if result.returncode != 0:
            stderr = (result.stderr or "")[:500]
            logger.error("Claude CLI failed (rc=%d): %s", result.returncode, stderr)
            raise RuntimeError(f"Claude CLI failed: {stderr}")

        return self._parse_response(result.stdout)

    def clear_session(self):
        """Reset session — next message starts a new conversation."""
        self._session_id = None
        logger.info("Claude session cleared")

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    def _resolve_working_dir(self) -> str:
        if self.working_dir:
            return str(Path(self.working_dir).expanduser())
        return str(Path.home())

    def _parse_response(self, stdout: str) -> str:
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            logger.warning("Non-JSON output from Claude, returning raw text")
            return stdout.strip() or "(empty response)"

        if isinstance(data, dict):
            if sid := data.get("session_id"):
                self._session_id = sid
            return data.get("result", "") or "(empty response)"

        if isinstance(data, list):
            for entry in reversed(data):
                if isinstance(entry, dict):
                    if sid := entry.get("session_id"):
                        self._session_id = sid
                    if entry.get("result"):
                        return entry["result"]
            return "(empty response)"

        return str(data)
