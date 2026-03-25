# claude-telegram-bridge

Access [Claude Code](https://docs.anthropic.com/en/docs/claude-code) from your phone via Telegram. Works on any Linux/Mac machine where Claude CLI is installed.

Claude Code offers remote access on iOS and Windows — but not Linux. This project fills the gap: a minimal Telegram bot on your PC that relays messages to the `claude` CLI and sends responses back to your phone.

## Architecture

```
Phone (Telegram)
    ↓ message
Telegram API (cloud)
    ↓ polling
bot.py (your PC)
    ↓ subprocess
claude -p CLI → Anthropic API → response
    ↓
bot.py → Telegram API → Phone
```

**No local AI needed.** Claude runs via Anthropic's API through your existing CLI authentication (subscription or API key).

## Prerequisites

- **Claude Code CLI** installed and authenticated (`claude` command works in terminal)
- **Python 3.11+**
- **Telegram bot token** — create one via [@BotFather](https://t.me/BotFather)

## Quick Start

```bash
# 1. Clone
git clone https://github.com/AdamSasen/claude-telegram-bridge.git
cd claude-telegram-bridge

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure
cp config.example.yaml config.yaml
# Edit config.yaml — add your bot token and Telegram user ID

# 4. Run
python bot.py

# 5. Open Telegram → your bot → /claude → start chatting
```

**Tip:** Send `/start` to the bot to see your Telegram user ID.

## Commands

| Command   | Description                                       |
|-----------|---------------------------------------------------|
| `/start`  | Welcome message + your user ID                    |
| `/claude` | Connect — messages forwarded to Claude Code       |
| `/local`  | Disconnect — stop forwarding                      |
| `/clear`  | Reset Claude session (new conversation)           |
| `/status` | Show current mode, session, and permission info   |
| `/accept` | Retry last blocked action without restrictions    |

## Permission System

By default, the bridge uses `dontAsk` permission mode with a whitelist of safe tools. This means:

- **Allowed by default:** reading files, editing files, writing files, searching code
- **Blocked by default:** shell commands (rm, git push, npm install, etc.)
- **When blocked:** bot shows error + "Send /accept to retry"
- **`/accept`:** re-runs the last message with all permissions (one-shot override)

This gives you **explicit control from your phone** over dangerous operations.

### Permission Modes

| Mode | Behavior |
|------|----------|
| `dontAsk` | Only whitelisted tools work. Everything else blocked. **(recommended)** |
| `acceptEdits` | Auto-accepts file reads/writes and shell commands. Permissive. |
| `bypassPermissions` | Everything allowed. No safety net. |

### Customizing Allowed Tools

Edit `allowed_tools` in `config.yaml`:

```yaml
allowed_tools:
  - "Read"
  - "Edit"
  - "Write"
  - "Glob"
  - "Grep"
  - "Bash(cat *)"
  - "Bash(ls *)"
  - "Bash(pwd)"
  # Add more as needed:
  # - "Bash(npm test)"
  # - "Bash(git status)"
  # - "Bash(python *)"
```

## How It Works

1. Bot receives your Telegram message
2. Spawns `claude -p --output-format json --permission-mode dontAsk --model <model> <message>`
3. Parses JSON response, extracts `result` and `session_id`
4. Sends result back to Telegram (chunked if > 4000 chars)
5. On next message, uses `--resume <session_id>` for conversation continuity
6. If Claude needs a blocked permission, you approve with `/accept`

The `-p` flag runs Claude in **pipe mode** (non-interactive, no terminal UI).

## Configuration

**config.yaml** (copy from `config.example.yaml`):

```yaml
telegram:
  bot_token: "123456:ABC..."       # From @BotFather
  allowed_user_ids: [123456789]    # Your Telegram user ID(s)

claude:
  model: "sonnet"                  # sonnet, opus, haiku
  timeout: 180                     # Max wait seconds
  working_dir: "~/my-project"      # Where Claude executes commands
  permission_mode: "dontAsk"       # See Permission Modes above
  allowed_tools: [...]             # Whitelisted tools for dontAsk mode
```

**Environment variables** (override config file):

- `TELEGRAM_BOT_TOKEN` — bot token

## Running as a Service

```bash
cat << 'EOF' | sudo tee /etc/systemd/system/claude-telegram.service
[Unit]
Description=Claude Telegram Bridge
After=network.target

[Service]
Type=simple
User=YOUR_USERNAME
WorkingDirectory=/path/to/claude-telegram-bridge
ExecStart=/usr/bin/python3 bot.py
Restart=on-failure
RestartSec=10
Environment=TELEGRAM_BOT_TOKEN=your_token_here

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now claude-telegram
```

## Security

- **User whitelist** — only `allowed_user_ids` can interact with the bot
- **Permission control** — dangerous commands blocked by default, require explicit `/accept`
- **No secrets in repo** — `config.yaml` and `.env` are gitignored
- **Local execution** — Claude runs on your machine, nothing sent to third parties beyond Telegram API and Anthropic API
- **Set `allowed_user_ids`** — leaving it empty allows anyone who discovers your bot to use it

## License

MIT
