---
status: pending
priority: p3
issue_id: "022"
tags: [code-review, frontend, html-first-magazine]
dependencies: ["018"]
---

# Move Inline Style Colors to CSS Custom Properties

## Problem Statement

Current Python code sets `style="background:#dc2626"` on domain pills and other elements. These inline styles override CSS custom property themes, blocking dark mode from working on these elements.

## Findings

**Source:** Performance Oracle

Change from:
```html
<span style="background:#dc2626">arxiv.org</span>
```
To:
```html
<span class="domain-pill" style="--pill-bg:#dc2626">arxiv.org</span>
```
With CSS:
```css
.domain-pill { background: var(--pill-bg); }
@media (prefers-color-scheme: dark) {
  .domain-pill { filter: brightness(1.2) saturate(0.9); }
}
```

## Acceptance Criteria

- [ ] Domain pills use `--pill-bg` CSS variable
- [ ] Badge colors use CSS variables
- [ ] Dark mode `filter` adjusts all pill colors automatically
- [ ] No hardcoded `background:` in inline styles for themed elements

## Work Log

| Date | Action | Learnings |
|------|--------|-----------|
| 2026-03-28 | Created from plan review | Performance oracle + best practices researcher aligned |
