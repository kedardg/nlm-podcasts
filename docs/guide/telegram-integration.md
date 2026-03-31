# Telegram Bot Connection

## How Messages Flow

```
User sends "/generate report" on Telegram
        │
        ▼
OpenClaw Gateway (WebSocket server, port 18789)
        │
        ├── Identifies Telegram channel + chat ID ($TELEGRAM_CHAT_ID)
        ├── Creates/resumes session for this chat
        ├── Injects workspace files (TOOLS.md, RESEARCH.md, etc.) into system prompt
        │
        ▼
LLM Agent Turn (GPT-4o)
        │
        ├── TOOLS.md says: "Run router.py first"
        ├── Agent calls: python3 router.py "generate report"
        ├── Router returns: {"handled": false, "reason": "command_generate_fetch", "artifact": "report"}
        ├── TOOLS.md says: follow "Fetch Latest Pipeline"
        ├── Agent executes fetch_latest.py, nlm download, etc.
        │
        ▼
Agent outputs text → auto-routed to Telegram as reply
Agent calls `openclaw message send --media` → file delivered to Telegram
```

## The Router Layer (`router.py`)

Every incoming Telegram message is first passed through the deterministic router. This is critical — it prevents the LLM from wasting tokens on commands that have fixed behavior.

### How It Works

```python
# In TOOLS.md, the LLM is told to always run this first:
python3 $PROJECT_ROOT/router.py "<user_message>"
```

The router (`router.py:749-818`) strips leading `/`, parses `command args`, and dispatches:

**Handled directly (no LLM needed):**
- `manual`, `help`, `start` → `cmd_manual()` — returns formatted help text
- `track @user` → `cmd_track()` — adds to accounts.json
- `track off @user` → `cmd_untrack()` — pauses tracking
- `list accounts|sources|categories|newsletters` → `cmd_list()` — reads JSON, formats summary
- `status [triage|news|daily]` → `cmd_status()` — system overview
- `addcategory` → `cmd_addcategory()` — adds to categories.json
- `note <text>` → `cmd_note()` — adds text source to sources.json
- `subscribe / subscribe off` → `cmd_subscribe()` / `cmd_unsubscribe()`
- `model [name]` → `cmd_model()` — shows/switches LLM
- `reset` → `cmd_reset()` — clears context
- Raw URLs → `cmd_ingest_url()` — adds to sources.json with auto-categorization
- `generate` (no subcommand) → returns usage help

**Routed to LLM (needs intelligence):**
- `generate report|podcast|report+podcast` → `command_generate_fetch` — fetch existing artifacts
- `generate news` → `command_generate_news_podcast` — generate podcast from news notebook
- `generate daily` → `command_generate_daily` — create daily feed
- `process` → `command_process` — full weekly pipeline
- `chat [category:] <question>` → `command_chat` — RAG query
- Plain text (no command) → `chat_query` — research Q&A

### Router Output Format

```json
// Handled — LLM just echoes the response
{"handled": true, "action": "track", "response": "Now tracking @karpathy...\\n\\nDone."}

// Not handled — LLM follows the pipeline in TOOLS.md
{"handled": false, "route": "llm", "reason": "command_generate_fetch", "command": "generate", "args": "report", "artifact": "report"}
```

## Sending Files to Telegram

Text replies from the LLM are auto-routed to Telegram by OpenClaw. But files (PDFs, MP3s) require an explicit CLI command:

```bash
openclaw message send --channel telegram --target $TELEGRAM_CHAT_ID --media /path/to/file --message "caption"
```

**Key constraints:**
- `--target $TELEGRAM_CHAT_ID` is the numeric Telegram chat ID (set in `.env`; also found in sessions.json under `deliveryContext.to`, strip `telegram:` prefix)
- Files MUST be under `~/.openclaw/workspace/` — OpenClaw only allows media delivery from this directory
- If you try to send a file from another directory, it fails with "not under an allowed directory"
- The LLM must be explicitly told to use this command in TOOLS.md — otherwise it just prints the file path as text

## Registered Telegram Commands

7 slash commands are registered with @BotFather. Old bare-text forms still work via backwards compatibility in the router.

| Telegram Command | Router Handler | Type |
|---|---|---|
| `/manual` | `cmd_manual()` | Direct response |
| `/track @user` | `cmd_track()` | Direct response |
| `/list sources` | `cmd_list()` | Direct response |
| `/status` | `cmd_status()` | Direct response |
| `/generate report` | `cmd_generate()` → LLM | Pipeline |
| `/process` | LLM | Pipeline |
| `/chat <question>` | LLM | RAG query |

## Session Lifecycle

- Each Telegram chat gets a unique session key
- Sessions accumulate the full conversation history
- Context compaction triggers when approaching token limits
- `/reset` clears the session context
- Stale sessions cause the most common issues — the LLM repeats old wrong answers

### Debugging Stale Sessions

```bash
# View active sessions
openclaw sessions --json

# Nuclear reset (when LLM keeps giving wrong answers)
echo '{}' > ~/.openclaw/agents/main/sessions/sessions.json
rm ~/.openclaw/agents/main/sessions/*.jsonl
openclaw gateway restart
```

## Common Issues

### "Pipeline not connected" or wrong pipeline behavior
The LLM reads TOOLS.md for pipeline instructions. If TOOLS.md has stale content, the LLM follows wrong steps. Always check `~/.openclaw/workspace/TOOLS.md` first.

### Files not appearing in Telegram
The LLM printed the file path as text instead of calling `openclaw message send --media`. Fix: ensure TOOLS.md includes explicit media delivery commands after every download step.

### Bot not responding at all
1. Check daemon: `openclaw daemon status`
2. Check gateway: `openclaw health --json`
3. Check Telegram token in `~/.openclaw/openclaw.json`
4. Check channel: `openclaw channels status --probe`
