# Adding Tools to ClawdBot

How to create new commands and skills, wire them into the bot, and deploy changes.

## The Three Layers

Every Telegram message flows through three layers. Where you add code depends on what kind of tool you're building.

```
Telegram message arrives
        │
        ▼
[Layer 1] router.py          ← Deterministic commands (instant, no LLM)
        │
        ▼ (if not handled)
[Layer 2] TOOLS.md            ← Pipeline instructions (tells LLM what steps to run)
        │
        ▼ (for multi-step workflows)
[Layer 3] skills/*/SKILL.md   ← Skill definitions (complex workflows with multiple tools)
```

| Layer | When to use | Examples |
|-------|------------|----------|
| **router.py** | Command has fixed behavior, no LLM needed | `/track`, `/list`, `/status`, `/subscribe`, URL ingestion |
| **TOOLS.md** | Command needs LLM to execute a multi-step pipeline | `/generate report`, `/chat`, `/generate daily` |
| **SKILL.md** | Reusable workflow invoked by the LLM during pipelines | content-processor, notebooklm-orchestrator, news-digest |

Most new tools only need **Layer 1** (router command) or **Layer 1 + Layer 2** (router route + TOOLS.md pipeline). Layer 3 skills are for complex workflows that run as part of the weekly/daily pipelines.

---

## Option A: Add a Deterministic Command (router.py only)

Use this when the command has predictable behavior — read/write JSON, format a summary, toggle a setting. No LLM involvement.

### Step 1: Write the handler function

Open `router.py` and add a `cmd_yourcommand()` function. Follow the existing pattern:

```python
def cmd_yourcommand(args):
    """Handle /yourcommand <args>."""
    # 1. Parse args
    if not args:
        return {"handled": True, "action": "yourcommand",
                "response": "Usage: /yourcommand <something>"}

    # 2. Do the work (read/write JSON, etc.)
    data_path = DATA_DIR / "sources.json"
    with open(data_path) as f:
        data = json.load(f)

    # ... modify data ...

    with open(data_path, "w") as f:
        json.dump(data, f, indent=2)

    # 3. Return response
    return {"handled": True, "action": "yourcommand",
            "response": f"Done. Updated {len(data)} items.\n\nDone."}
```

Key rules:
- Return `{"handled": True, "response": "..."}` — the bot replies with exactly this text
- End the response with `\n\nDone.` (convention — tells the user the command completed)
- Use `DATA_DIR` (`$PROJECT_ROOT/data/`) for all data file paths
- For URL inputs, use `normalize_url_deep()` from `url_utils.py` for dedup

### Step 2: Wire it into the dispatch table

In the `route()` function (around line 810), add your command to the dispatch logic. Commands are checked in order:

```python
def route(message):
    msg = message.strip()
    # ... existing parsing ...

    cmd = parts[0]
    args = rest_of_message

    # Existing commands...
    if cmd in ("manual", "help", "start"):
        return cmd_manual()
    if cmd == "track":
        # ...

    # ADD YOUR COMMAND HERE (before the LLM_COMMANDS check)
    if cmd == "yourcommand":
        return cmd_yourcommand(args)

    # Commands that need LLM (must come after all deterministic handlers)
    if cmd in LLM_COMMANDS:
        return {"handled": False, "route": "llm", ...}
```

**Important**: Add your command BEFORE the `if cmd in LLM_COMMANDS` check. Otherwise `chat` and `process` would swallow it.

### Step 3: Update the help text

Find `cmd_manual()` and add your command to the help output:

```python
def cmd_manual():
    return {
        "handled": True,
        "action": "manual",
        "response": (
            "📋 Commands:\n"
            # ... existing commands ...
            "• /yourcommand <args> — description\n"
            # ...
        ),
    }
```

### Step 4: Test locally

```bash
# Test your command
python3 router.py "yourcommand some args"

# Verify JSON output
python3 router.py "yourcommand" | python3 -m json.tool

# Test edge cases
python3 router.py "yourcommand"          # no args
python3 router.py "yourcommand bad input" # invalid input
```

### Step 5: Deploy (see "Deploying Changes" below)

---

## Option B: Add an LLM-Routed Command (router.py + TOOLS.md)

Use this when the command needs the LLM to execute steps — run scripts, call `nlm`, process output, make decisions.

### Step 1: Add the router route

The router doesn't handle the command — it tells the LLM which pipeline to follow.

```python
def cmd_yourpipeline(args):
    """Route /yourpipeline to LLM with pipeline instructions."""
    return {
        "handled": False,        # NOT handled — LLM takes over
        "route": "llm",
        "reason": "command_yourpipeline",  # This string maps to a section in TOOLS.md
        "command": "yourpipeline",
        "args": args,
    }
```

Wire it into `route()` the same way as Option A:

```python
if cmd == "yourpipeline":
    return cmd_yourpipeline(args)
```

### Step 2: Add the pipeline to TOOLS.md

Edit `~/.openclaw/workspace/TOOLS.md`. Add a routing entry and a pipeline section.

**Add to the routing table** (top of TOOLS.md):

```markdown
- `"handled": false, "reason": "command_yourpipeline"` → Follow the **Your Pipeline** below.
```

**Add the pipeline section**:

```markdown
## Your Pipeline (`command_yourpipeline`)

Description of what this pipeline does.
Do NOT use `sessions_send`. Just reply with text directly.

Reply "Starting your pipeline..." first, then run these steps:

**Step 1: Description**
​```bash
$PROJECT_ROOT/.venv/bin/python3 $PROJECT_ROOT/scripts/your_script.py
​```
Parse the output. If exit code 1, reply with the error and stop.

**Step 2: Description**
​```bash
~/.local/bin/nlm some-command <args>
​```

**Step 3: Deliver to Telegram** (if producing files)
​```bash
openclaw message send --channel telegram --target $TELEGRAM_CHAT_ID --media ~/.openclaw/workspace/output.pdf --message "Your output"
​```

**Step 4: Reply with summary**
Reply with what was done, results, and any links.
```

**Pipeline writing rules**:
- Use full paths for everything (`~/.local/bin/nlm`, `.venv/bin/python3`, absolute project paths)
- Always call `scripts/nlm_ensure_auth.sh` before any `nlm` command
- Files for Telegram delivery MUST be in `~/.openclaw/workspace/`
- Tell the LLM to reply with progress messages between steps
- Include error handling ("If exit code 1, reply with error and stop")
- Never ask the LLM to generate dates — use `$(date +%Y-%m-%d)` or compute in Python

### Step 3: Test

```bash
# Verify the router returns the right reason
python3 router.py "yourpipeline some args"
# Should show: {"handled": false, "reason": "command_yourpipeline", ...}
```

Then deploy and test via Telegram (see "Deploying Changes" below).

---

## Option C: Add a Skill (skills/*/SKILL.md)

Use this for complex workflows that are invoked by the LLM as part of larger pipelines (like the weekly cron). Skills are NOT called directly by users — they're called by the LLM when TOOLS.md or another skill tells it to "Execute the X skill."

### Step 1: Create the skill directory

```bash
mkdir -p $PROJECT_ROOT/skills/your-skill
```

### Step 2: Write SKILL.md

Create `skills/your-skill/SKILL.md`. This is the skill's instruction set — the LLM reads it and follows the steps.

```markdown
# Your Skill Name

## Purpose
One-line description of what this skill does.

## When This Runs
- As part of the weekly pipeline (Step N)
- Or: on-demand via `/yourcommand`

## Prerequisites
- NotebookLM auth must be valid (run nlm_ensure_auth.sh first)
- data/sources.json must have queued items

## Execution Flow

### Step 1: Load data
Read `data/sources.json` and filter for items with `status: "queued"`.

If no items found, reply: "No queued sources to process." and stop.

### Step 2: Process
​```bash
$PROJECT_ROOT/.venv/bin/python3 $PROJECT_ROOT/scripts/your_script.py --auto
​```

### Step 3: Update data
Update status in `sources.json`:
- Set `status: "processed"` for each processed item

### Step 4: Output
Reply with a summary of what was processed.

## Error Handling
- If a single item fails, log the error and CONTINUE with remaining items
- Never stop the pipeline for a single-item failure
- Report all errors in the final summary
```

**SKILL.md writing conventions**:
- Start with purpose and when-it-runs context
- Number all steps clearly
- Include exact shell commands with full paths
- Specify error handling for each step
- End with what the skill outputs/replies

### Step 3: Reference the skill in the pipeline

If this skill runs as part of the weekly cron, update the cron message in `config/cron-jobs.json` and the Process Pipeline section in TOOLS.md:

```markdown
## Process Pipeline (full)

Run these steps in order:
1. Run triage: `data_triage.py --auto`
2. Execute the content-processor skill
3. Execute your-skill                          ← ADD HERE
4. Execute the notebooklm-orchestrator skill
5. Execute the digest-deliver skill
6. Reply with final summary
```

Also update the cron job definition:

```bash
openclaw cron remove weekly-research-digest
openclaw cron add \
  --name "weekly-research-digest" \
  --cron "0 9 * * 6" \
  --tz "America/New_York" \
  --session isolated \
  --message "Run the weekly research digest: 1) newsletter-monitor. 2) x-monitor. 3) data_triage.py --auto. 4) content-processor. 5) your-skill. 6) notebooklm-orchestrator. 7) news-digest. 8) digest-deliver." \
  --announce --channel telegram
```

---

## Adding a Helper Script

If your tool needs a Python script, put it in `scripts/` (for utilities) or at the project root (for major pipeline scripts).

### Script template

```python
#!/usr/bin/env python3
"""Short description of what this script does."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent  # if in scripts/
DATA_DIR = PROJECT_ROOT / "data"

def main():
    parser = argparse.ArgumentParser(description="Your script description")
    parser.add_argument("--auto", action="store_true", help="Run in automated mode")
    parser.add_argument("--dry-run", action="store_true", help="Preview without saving")
    args = parser.parse_args()

    # Load data
    sources_path = DATA_DIR / "sources.json"
    with open(sources_path) as f:
        sources = json.load(f)

    # Do work...
    result = {"processed": 0, "errors": []}

    # Save (unless dry-run)
    if not args.dry_run:
        with open(sources_path, "w") as f:
            json.dump(sources, f, indent=2)

    # Output JSON for the LLM to parse
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
```

**Script conventions**:
- Output JSON to stdout (the LLM parses it)
- Errors to stderr
- Exit code 0 = success, 1 = failure
- Support `--dry-run` for safe testing
- Use `DATA_DIR` for all data paths
- Compute dates with `datetime.now(timezone.utc)` — never let the LLM generate dates
- If calling `nlm`, use the full path `~/.local/bin/nlm`

### Running scripts in pipelines

Always use the venv Python and full paths:

```bash
# In TOOLS.md or SKILL.md
$PROJECT_ROOT/.venv/bin/python3 $PROJECT_ROOT/scripts/your_script.py --auto
```

If the script needs WeasyPrint (PDF generation):

```bash
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib $PROJECT_ROOT/.venv/bin/python3 $PROJECT_ROOT/your_script.py
```

### Installing new Python dependencies

```bash
cd $PROJECT_ROOT
uv pip install new-package-name
```

---

## Deploying Changes

After making changes, you need to clear the bot's cached session and restart the gateway so it picks up the new TOOLS.md.

### Step 1: Clear sessions

The LLM caches previous conversation context (including old TOOLS.md instructions). Stale sessions are the #1 cause of "the bot ignores my changes."

```bash
# Clear all session state
echo '{}' > ~/.openclaw/agents/main/sessions/sessions.json
rm -f ~/.openclaw/agents/main/sessions/*.jsonl
```

### Step 2: Restart the gateway

```bash
# If running as a launchd service
openclaw gateway restart

# If the service isn't installed, stop and restart manually
# Find the running process
lsof -i :18789
# Kill it
kill <pid>
# Start fresh
openclaw gateway --port 18789 --bind loopback
```

### Step 3: Verify

```bash
# Check gateway is healthy
openclaw health --json

# Check sessions are cleared
# Should show "count": 0
openclaw health --json 2>&1 | grep -A2 sessions
```

### Step 4: Test via Telegram

Send your new command on Telegram. The bot starts a fresh session, reads the updated TOOLS.md, and should follow the new instructions.

### Quick deploy one-liner

```bash
echo '{}' > ~/.openclaw/agents/main/sessions/sessions.json && rm -f ~/.openclaw/agents/main/sessions/*.jsonl && openclaw gateway restart
```

If gateway restart says "not loaded", find and kill the process manually:

```bash
echo '{}' > ~/.openclaw/agents/main/sessions/sessions.json && rm -f ~/.openclaw/agents/main/sessions/*.jsonl && kill $(lsof -ti :18789) 2>/dev/null; openclaw gateway --port 18789 --bind loopback
```

---

## Registering Telegram Slash Commands

If you want your command to appear in Telegram's `/` autocomplete menu, register it with @BotFather:

1. Open Telegram, message @BotFather
2. Send `/setcommands`
3. Select your bot (@weekly_research_bot)
4. Send the full command list:

```
manual - Show command reference
track - Track an X/Twitter account
list - Browse data (accounts, sources, categories, newsletters)
status - System overview
generate - Fetch reports, podcasts, daily feed
process - Run full pipeline
chat - Search your research via RAG
yourcommand - Your new command description
```

This only affects the autocomplete menu — the bot handles commands regardless of BotFather registration.

---

## Updating CLAUDE.md

After adding a new tool, update the root `CLAUDE.md` so that Claude Code (and the bot's RESEARCH.md symlink) know about it:

- Add the command to the **Telegram Commands** table
- If you added a skill, add it to the **Skills** section
- If you added a script, add it to the relevant section (Scripts, Triage, etc.)
- If you added a data file, add it to the **Data Files** table

---

## Checklist: New Tool

- [ ] Decided which layer(s): router.py / TOOLS.md / SKILL.md
- [ ] Wrote handler function or pipeline section
- [ ] Wired into router dispatch table (before `LLM_COMMANDS` check)
- [ ] Updated `cmd_manual()` help text
- [ ] Tested locally: `python3 router.py "yourcommand args"`
- [ ] Updated TOOLS.md (if LLM-routed)
- [ ] Updated CLAUDE.md with new command/skill
- [ ] Cleared sessions + restarted gateway
- [ ] Tested via Telegram
- [ ] (Optional) Registered with @BotFather for autocomplete
- [ ] (Optional) Updated cron job if skill is part of weekly pipeline

## Existing Skills Reference

| Skill | Type | Layer | What it does |
|-------|------|-------|-------------|
| research-ingest | Entry point | Router + TOOLS.md | Routes all Telegram input through router.py |
| x-monitor | Pipeline step | SKILL.md | Scrapes tracked X/Twitter accounts for links |
| newsletter-monitor | Pipeline step | SKILL.md | Fetches emails via IMAP, extracts and classifies links |
| content-processor | Pipeline step | SKILL.md | Categorizes sources, creates companion notes |
| notebooklm-orchestrator | Pipeline step | SKILL.md | Creates notebooks, adds sources, generates podcasts/reports |
| news-digest | Pipeline step | SKILL.md | Weekly news briefing + podcast from news-queue.json |
| daily-digest | Pipeline + command | TOOLS.md + SKILL.md | Daily feed PDF + podcast (6 PM ET cron or `/generate daily`) |
| digest-deliver | Pipeline step | SKILL.md | Downloads artifacts, converts to PDF, delivers via Telegram |
| chat-rag | Command | TOOLS.md | RAG-powered Q&A across all notebooks |
| aisa-twitter-api | External | ClawHub | Twitter API access (installed via `npx clawhub install`) |
