#!/bin/bash
# Thin loader — auto-detects PROJECT_ROOT, then sources .env for all config.
# Works in both bash and zsh. Sourced by scripts/ and SKILL.md preambles.

# Auto-detect PROJECT_ROOT from this file's location
if [ -z "${PROJECT_ROOT:-}" ]; then
  _src="${BASH_SOURCE[0]:-${(%):-%x}}" 2>/dev/null || _src=""
  if [ -n "$_src" ]; then
    export PROJECT_ROOT="$(cd "$(dirname "$_src")/.." && pwd)"
  fi
fi

# Source .env (the single config file)
if [ -n "${PROJECT_ROOT:-}" ] && [ -f "$PROJECT_ROOT/.env" ]; then
  set -a
  . "$PROJECT_ROOT/.env"
  set +a
fi

# Export PROJECT_ROOT (fail if still unset)
export PROJECT_ROOT="${PROJECT_ROOT:?PROJECT_ROOT must be set — create .env from .env.example}"
