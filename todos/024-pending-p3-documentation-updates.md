---
status: pending
priority: p3
issue_id: "024"
tags: [code-review, docs, html-first-magazine]
dependencies: ["017", "020"]
---

# Documentation Updates for HTML-First Magazine

## Problem Statement

Per CLAUDE.md Documentation Maintenance table, several files need updating when pipeline steps or data files change.

## Acceptance Criteria

- [ ] CLAUDE.md: Data Files table adds `.html` output
- [ ] CLAUDE.md: Daily Feed Commands section updated (new flags)
- [ ] CLAUDE.md: Quick Reference table updated
- [ ] CLAUDE.md: Utility Modules table adds `html_renderer.py`
- [ ] CLAUDE.md: Security Considerations mentions HTML escaping requirement
- [ ] `skills/daily-digest/SKILL.md` updated for HTML output
- [ ] `~/.openclaw/workspace/TOOLS.md` updated for delivery commands
- [ ] `backup.sh` / `restore.sh` include `.html` in backup globs

## Work Log

| Date | Action | Learnings |
|------|--------|-----------|
| 2026-03-28 | Created from plan review | CLAUDE.md maintenance matrix requires these updates |
