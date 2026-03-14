# Guides

Technical deep-dives on each subsystem. Each guide explains how a component works, its key files, CLI commands, and common issues.

## Subsystems

| Guide | Description |
|-------|-------------|
| [openclaw-platform.md](openclaw-platform.md) | OpenClaw gateway, daemon, workspace files, sessions, cron, model config |
| [telegram-integration.md](telegram-integration.md) | Message flow, router layer, file delivery, session lifecycle |
| [notebooklm-mcp.md](notebooklm-mcp.md) | NotebookLM CLI, authentication, notebook/source/studio commands, data model |
| [news-pipeline.md](news-pipeline.md) | Daily feed and weekly news digest pipelines, news-queue schema |
| [source-tracking.md](source-tracking.md) | X/Twitter monitoring, newsletter email ingestion, link extraction |
| [classification.md](classification.md) | Two-layer classification (URL type + category), URL normalization, dedup |
| [rag-system.md](rag-system.md) | ChromaDB notebook routing, embedding pipeline, query pipeline |
| [generation.md](generation.md) | Report/podcast generation, fetch-latest pipeline, PDF rendering, weekly orchestrator |
| [setup.md](setup.md) | Prerequisites, installation order, common problems and solutions |
| [adding-tools.md](adding-tools.md) | How to create new commands and skills, wire them in, and deploy |
