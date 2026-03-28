# Documentation

This directory contains all project documentation, organized into three categories.

## Structure

| Folder | Purpose | When to update |
|--------|---------|----------------|
| [guide/](guide/) | Technical deep-dives on each subsystem | When a subsystem's behavior or API changes |
| [logs/](logs/) | Session notes, decisions, change history | After significant debugging sessions, redesigns, or architecture changes |
| [future/](future/) | Planned features and improvements | When planning new work or capturing ideas |

## Quick Links

- **Project overview** — [`CLAUDE.md`](../CLAUDE.md) (root, primary reference)
- **Getting started** — [`README.md`](../README.md) (quick start for macOS and Docker)
- **Pipeline instructions** — `~/.openclaw/workspace/TOOLS.md` (what the LLM reads)
- **Skill definitions** — `skills/*/SKILL.md`
- **Docker deployment** — [`guide/docker.md`](guide/docker.md) (full guide) | [`guide/setup.md`](guide/setup.md#docker-deployment-alternative) (quick section)

## Conventions

- Keep docs concise and factual. Code is the source of truth; docs explain *why* and *how things connect*.
- Use relative links between docs (e.g., `[setup](guide/setup.md)`).
- Log entries use `YYYY-MM-DD-short-description.md` naming.
- When you change a subsystem, update the corresponding guide. When you make a design decision, add a log entry.
