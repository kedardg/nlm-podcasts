---
status: pending
priority: p3
issue_id: "023"
tags: [code-review, quality, html-first-magazine]
dependencies: []
---

# Type SECTION_THEMES Dict with Dataclass + Fallback

## Problem Statement

`SECTION_THEMES` in `pdf_renderer.py` is an untyped `dict[str, dict[str, str]]` with magic string keys. A typo in a key name causes silent `KeyError`. No fallback theme exists for new sections.

## Findings

**Source:** Python Reviewer

```python
@dataclass(frozen=True, slots=True)
class SectionTheme:
    bg: str
    accent: str

DEFAULT_THEME = SectionTheme(bg="#f5f5f5", accent="#333333")

def get_section_theme(name: str) -> SectionTheme:
    return SECTION_THEMES.get(name, DEFAULT_THEME)
```

## Acceptance Criteria

- [ ] `SectionTheme` dataclass defined
- [ ] `DEFAULT_THEME` fallback prevents KeyError on unknown sections
- [ ] `get_section_theme()` used in both renderers

## Work Log

| Date | Action | Learnings |
|------|--------|-----------|
| 2026-03-28 | Created from plan review | Python reviewer flagged untyped dict |
