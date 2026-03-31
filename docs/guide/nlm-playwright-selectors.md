# NotebookLM Playwright Browser Automation: Selector & Sequence Reference

Research compiled 2026-03-04 from three GitHub repositories that automate NotebookLM via Playwright/browser automation.

## Repositories Analyzed

| # | Repo | Stars | Focus | Auth Approach |
|---|------|-------|-------|---------------|
| 1 | [israelbls/notebooklm-podcast-automator](https://github.com/israelbls/notebooklm-podcast-automator) | FastAPI service, full CRUD + audio | CDP to existing Chrome (persistent profile at `~/.notebooklm-chrome`) |
| 2 | [upamune/notebooklm-podcast-automator](https://github.com/upamune/notebooklm-podcast-automator) | CLI + Streamlit, create notebook + audio + Spotify upload | CDP to existing Chrome (user-launched) |
| 3 | [DataNath/notebooklm_source_automation](https://github.com/DataNath/notebooklm_source_automation) | CLI, batch URL source addition | `storage_state` (state.json) from manual login |

---

## Auth Approaches (Detailed)

### Repo 1 (israelbls) -- CDP Persistent Profile

```python
# browser.py - ChromeManager
# Launches Chrome with a dedicated profile directory for session persistence
launch_args = [
    chrome_binary,
    f"--remote-debugging-port={self.port}",       # default 9222
    f"--user-data-dir={user_data_dir}",           # default ~/.notebooklm-chrome
    "--disable-popup-blocking",
    "--no-first-run",
    "--no-default-browser-check",
]

# automator.py - Connect via CDP
self.browser = self.playwright.chromium.connect_over_cdp(
    f"http://{chrome_host}:{self.port}"
)
context = self.browser.contexts[0]     # reuse existing context (logged-in)
self.page = context.new_page()
self.page.set_viewport_size({"width": 1280, "height": 800})
self.page.goto(self.notebook_url, timeout=10000)
```

**Key details:**
- Auto-launches Chrome if `NOTEBOOKLM_AUTO_LAUNCH_CHROME=1` (default)
- 20-second timeout waiting for CDP endpoint
- Can also attach to already-running Chrome (`NOTEBOOKLM_AUTO_LAUNCH_CHROME=0`)
- First run: user logs into Google manually in the Chrome window; session persists in profile dir
- Navigates directly to a specific notebook URL (env var `NOTEBOOKLM_URL`)

### Repo 2 (upamune) -- CDP Manual Launch

```python
# core.py - connect via CDP (user must launch Chrome themselves)
self.browser = self.playwright.chromium.connect_over_cdp(f"http://localhost:{self.port}")
context = self.browser.contexts[0]
self.page = context.new_page()
self.page.set_viewport_size({"width": 1280, "height": 800})
self.page.goto("https://notebooklm.google.com/", timeout=60000)
```

**Key details:**
- User must manually launch Chrome: `/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222 --user-data-dir=./chrome-user-data --window-size=1280,800`
- Navigates to NLM homepage (not a specific notebook)
- Creates new notebook as part of the workflow (first URL triggers "Create new notebook")

### Repo 3 (DataNath) -- Playwright storage_state (state.json)

```python
# set_login_state.py - ONE-TIME manual login to save state
browser = p.chromium.launch(
    headless=False,
    channel="chrome",
    args=["--disable-blink-features=AutomationControlled"],
)
context = browser.new_context()
page = context.new_page()
page.goto("https://notebooklm.google.com/")
input("Log in manually and press ENTER when done...")
storage = context.storage_state(path="state.json")

# main.py - REUSE saved state (can be headless)
browser = sp.chromium.launch(headless=True, channel="chrome")
context = browser.new_context(storage_state=str(login_state_path))
page = context.new_page()
page.goto("https://notebooklm.google.com/")
```

**Key details:**
- Uses `--disable-blink-features=AutomationControlled` to avoid bot detection
- `state.json` stores cookies + localStorage (gitignored)
- Can run headless after initial login
- Persistence "works for a while" but eventually expires (author notes re-login needed occasionally)
- Does NOT use CDP -- launches its own Chromium instance

---

## Language Detection

Both israelbls and upamune detect the NLM UI language to localize selectors:

```python
# Both repos
lang = page.evaluate("document.documentElement.lang")
# Returns "en", "he", "ja", etc.
```

**israelbls** supports: English (`en`), Hebrew (`he`/`iw`)
**upamune** supports: English (`en`), Japanese (`ja`)

---

## Master Selector Reference Table

### Source Type Chips (Add Source Dialog)

| Element | Selector | Type | Stability | Used By |
|---------|----------|------|-----------|---------|
| Source type chip (generic) | `mat-chip-option, .mdc-evolution-chip, span.mat-mdc-chip-action` | CSS class | FRAGILE | israelbls |
| Source type chip label | `span.mdc-evolution-chip__text-label` | CSS class | FRAGILE | upamune, DataNath |
| Source type chip (fallback) | `page.get_by_text(type_text, exact=True)` | Text match | MODERATE | israelbls |

**Chip text values by language:**

| Source Type | English | Hebrew | Japanese |
|-------------|---------|--------|----------|
| Website | `Website` | `אתר` | `ウェブサイト` |
| YouTube | `YouTube` | `YouTube` | `YouTube` |
| Copied text | `Copied text` | `טקסט שהועתק` | -- |

### URL Input Field

| Selector | Type | Stability | Used By |
|----------|------|-----------|---------|
| `textarea[formcontrolname='newUrl']` | Angular form control | MODERATE (Angular-specific) | israelbls (primary), upamune, DataNath |
| `input[type='url'], input[placeholder*='http'], textarea[placeholder*='http']` | Standard HTML attrs | MODERATE | israelbls (fallback) |
| `page.get_by_placeholder("Paste URLs*")` | Placeholder text | FRAGILE (locale-dependent) | israelbls (fallback) |
| `[formcontrolname='newUrl']` | Angular form control | MODERATE | upamune, DataNath |

### Text Input Field (Copied Text)

| Selector | Type | Stability | Used By |
|----------|------|-----------|---------|
| `textarea[formcontrolname='textInput']` | Angular form control | MODERATE | israelbls |
| `textarea[formcontrolname='newText']` | Angular form control | MODERATE | israelbls (fallback) |
| `mat-dialog-container textarea` | CSS + element | MODERATE | israelbls (last resort) |

### Buttons

| Button | Selector | Type | Stability | Used By |
|--------|----------|------|-----------|---------|
| **Add sources** | `button:has-text('Add sources')` | Text match | MODERATE | israelbls |
| **Add source** | `page.get_by_role("button", name="Add source")` | ARIA role+name | STABLE | upamune, DataNath |
| **Create new notebook** | `page.get_by_role("button", name="Create new notebook")` | ARIA role+name | STABLE | upamune, DataNath |
| **Insert** | `page.get_by_role("button", name="Insert")` | ARIA role+name | STABLE | all three |
| **Generate** | `page.get_by_role("button", name="Generate")` | ARIA role+name | STABLE | upamune |
| **Generate** (dialog) | `mat-dialog-actions button:has-text('Generate')` | CSS + text | MODERATE | israelbls |
| **Close dialog** | `button:has(mat-icon:has-text('close'))` | CSS + icon text | FRAGILE | israelbls |
| **Close dialog** (fallback) | `mat-icon:has-text('close')` | CSS + icon text | FRAGILE | israelbls |
| **More options (source)** | `button.source-item-more-button` | CSS class | FRAGILE | israelbls |
| **More options (fallback)** | `button:has(mat-icon:has-text('more_vert'))` | CSS + icon text | FRAGILE | israelbls |
| **More options (audio)** | `button[aria-label='More']` | aria-label | STABLE | israelbls |
| **Play audio** | `button[aria-label='Play']` | aria-label | STABLE | israelbls |
| **Close audio player** | `button[aria-label='Close audio player']` | aria-label | STABLE | israelbls |

### Japanese-specific selectors (upamune)

| Button | Selector | Stability |
|--------|----------|-----------|
| Load audio | `button:has-text('読み込み')` | FRAGILE (text) |
| Play audio | `button[aria-label='音声を再生']` | STABLE (aria-label) |
| Audio options | `button[aria-label='オーディオ プレーヤーに関するその他のオプションを表示']` | STABLE (aria-label) |
| Download link | `a[mat-menu-item]:has-text('ダウンロード')` | MODERATE |

### Dialogs & Containers

| Element | Selector | Type | Stability | Used By |
|---------|----------|------|-----------|---------|
| Add source dialog | `mat-dialog-container` | Angular component | MODERATE | israelbls |
| Source item | `div.single-source-container` | CSS class | FRAGILE | israelbls, DataNath |
| Loading spinner | `.mat-progress-spinner` | CSS class | FRAGILE | upamune |
| Loading spinner | `.mat-mdc-progress-spinner` | CSS class | FRAGILE | DataNath |
| Artifact library | `artifact-library` | Custom element | MODERATE | israelbls |
| Artifact children | `artifact-library > *` | Custom element children | MODERATE | israelbls |
| Notebook title | `h1.notebook-title` | CSS class | FRAGILE | upamune |
| Summary content | `div.summary-content` | CSS class | FRAGILE | upamune |
| Title input | `.title-input` | CSS class | FRAGILE | DataNath |

### Menu Items

| Item | Selector | Type | Stability | Used By |
|------|----------|------|-----------|---------|
| Remove source | `page.get_by_role("menuitem", name="Remove source")` | ARIA role+name | STABLE | israelbls |
| Delete (artifact) | `page.get_by_role("menuitem", name="Delete")` | ARIA role+name | STABLE | israelbls |
| Download (artifact) | `page.get_by_role("menuitem", name="Download")` | ARIA role+name | STABLE | israelbls |
| Confirm delete | `page.get_by_role("button", name="Delete")` | ARIA role+name | STABLE | israelbls |

### Audio Generation Config (Dialog)

| Element | Selector | Type | Stability | Used By |
|---------|----------|------|-----------|---------|
| Edit/pencil icon | `mat-icon:has-text('edit'), mat-icon.edit-button-icon` | CSS + icon text | FRAGILE | israelbls |
| Edit button (fallback) | `button:has(mat-icon:has-text('edit'))` | CSS + icon text | FRAGILE | israelbls |
| Style radio button | `mat-radio-button:has-text('{style_text}')` | Angular + text | MODERATE | israelbls |
| Language dropdown | `mat-select` | Angular component | MODERATE | israelbls |
| Language option | `mat-option:has-text('{language}')` | Angular + text | MODERATE | israelbls |
| Prompt textarea | `textarea[placeholder*='Things to try']` | Placeholder text | FRAGILE | israelbls |
| Prompt textarea (fallback) | `mat-dialog-container textarea` (last) | CSS container | MODERATE | israelbls |
| Checkbox (source) | `input.mdc-checkbox__native-control.mdc-checkbox--selected` | CSS class | FRAGILE | DataNath |

### Audio Style Radio Button Values

| Style Key | English Text | Hebrew Text |
|-----------|-------------|-------------|
| `deep_dive` | `Deep Dive` | `ירידה לפרטים` |
| `summary` | `Summary` | `תקציר` |
| `critique` | `Critique` | `ביקורת` |
| `debate` | `Debate` | `דיבייט` |

### Status Detection (Audio)

| Status | Detection Method | Used By |
|--------|-----------------|---------|
| Generating | `"sync" in text_content` OR text contains "Generating" | israelbls |
| Completed | `"play_arrow" in text_content` OR `mat-icon:has-text('play_arrow')` visible | israelbls |
| Failed | `"error" in text_content.lower()` | israelbls |

---

## Complete Click Sequences

### Sequence 1: Create New Notebook + Add First URL (upamune, DataNath)

```
1. Navigate to https://notebooklm.google.com/
2. page.wait_for_load_state()
3. Click: page.get_by_role("button", name="Create new notebook")
   - Wait: state="attached", expect to_be_enabled()
4. sleep(1)  -- wait for source dialog to appear
5. Click source type chip: page.locator("span.mdc-evolution-chip__text-label", has_text="Website")
   - Wait: state="attached"
6. Fill URL: page.locator("[formcontrolname='newUrl']").fill(url)
   - Wait: state="attached", expect to_be_enabled()
7. Click: page.get_by_role("button", name="Insert")
   - Wait: expect to_be_enabled()
8. sleep(2)
9. Wait for spinner: page.locator(".mat-progress-spinner").wait_for(state="detached", timeout=30000)
10. sleep(2)
```

### Sequence 2: Add Subsequent URL (upamune, DataNath)

```
1. Click: page.get_by_role("button", name="Add source")
   - Wait: state="attached", expect to_be_enabled()
2. sleep(1)
3. Click source type chip: span.mdc-evolution-chip__text-label has_text="Website"|"YouTube"
   - Wait: state="attached"
4. Fill URL: [formcontrolname='newUrl'].fill(url)
   - Wait: state="attached", expect to_be_enabled()
5. Click: page.get_by_role("button", name="Insert")
   - Wait: expect to_be_enabled()
6. Wait for source container: div.single-source-container (last)
   - Wait: state="attached"
7. Wait for spinner inside container: .mat-mdc-progress-spinner
   - Wait: state="detached"
8. Wait for checkbox: input.mdc-checkbox__native-control.mdc-checkbox--selected
   - Wait: state="attached"
   - expect: NOT to_have_attribute("ariaLabel", url)  # label changes when processed
9. page.wait_for_timeout(1200)
```

### Sequence 3: Add URL Source (israelbls -- to existing notebook)

```
1. Navigate to notebook URL (already done at connect time)
2. Check dialog: page.locator("mat-dialog-container").last.is_visible()
3. If not open, click: button:has-text('Add sources')
4. wait_for_timeout(500)
5. Click source type chip:
   - Primary: page.locator("mat-chip-option, .mdc-evolution-chip, span.mat-mdc-chip-action", has_text="Website")
   - Fallback: page.get_by_text("Website", exact=True)
6. Fill URL:
   - Primary: textarea[formcontrolname='newUrl']
   - Fallback: input[type='url'], input[placeholder*='http'], textarea[placeholder*='http']
   - Fallback: page.get_by_placeholder("Paste URLs*")
7. wait_for_timeout(500)
8. Click insert:
   - Primary: page.get_by_role("button", name="Insert")
   - Fallback: inp.press("Enter")
9. wait_for_timeout(100)
10. Close dialog: button:has(mat-icon:has-text('close'))
```

### Sequence 4: Add Text Source (israelbls)

```
1. Open dialog (same as Sequence 3, steps 2-4)
2. Click "Copied text" chip:
   - Primary: mat-chip-option, .mdc-evolution-chip, span.mat-mdc-chip-action has_text="Copied text"
   - Fallback: page.get_by_text("Copied text", exact=True)
3. Find textarea in dialog:
   - Primary: textarea[formcontrolname='textInput']
   - Fallback: textarea[formcontrolname='newText']
   - Fallback: mat-dialog-container textarea (any)
4. textarea.fill(text_content)
5. wait_for_timeout(500)
6. Click insert:
   - Primary: page.get_by_role("button", name="Insert")
   - Fallback: textarea.press("Enter")
7. wait_for_timeout(100)
8. Close dialog
```

### Sequence 5: Generate Audio Overview (israelbls -- with options)

```
1. Click edit/pencil icon to open config dialog:
   - Primary: mat-icon:has-text('edit'), mat-icon.edit-button-icon -- click(force=True)
   - Fallback: button:has(mat-icon:has-text('edit'))
2. Wait: page.wait_for_selector("mat-dialog-container", state="visible")
3. [Optional] Set style (e.g., "deep_dive"):
   - Click: mat-radio-button:has-text('Deep Dive')
4. [Optional] Set language:
   - Click: mat-select (first)
   - Wait: page.wait_for_selector("mat-option", state="visible")
   - Click: mat-option:has-text('{language}')
   - If not found: keyboard.press("Escape")
5. [Optional] Set custom prompt:
   - Primary: textarea[placeholder*='Things to try']
   - Fallback: mat-dialog-container textarea (last)
   - textarea.fill(prompt)
6. Click generate:
   - Primary: mat-dialog-actions button:has-text('Generate') (last)
   - Fallback: mat-dialog-actions button (last)
7. Wait for dialog to close: mat-dialog-container state="hidden" timeout=5000
8. sleep(2)
9. Count artifacts: page.locator(".artifact-library-container").count()
   - Job ID = count (1-based index)
```

### Sequence 6: Generate Audio Overview (upamune -- simple)

```
1. Get generate button: page.get_by_role("button", name="Generate")
2. Wait: expect to_be_enabled(timeout=30000)
   - If timeout, sleep(10) and retry with timeout=10000
3. Click generate button
4. Confirm started: expect generate_button to_be_disabled(timeout=2000)
```

### Sequence 7: Poll Audio Status (israelbls)

```
1. Locate artifact library: page.locator("artifact-library")
2. Get children: parent.locator(":scope > *")
3. Get item at index (job_id - 1)
4. Read: item.inner_text()
5. Check status:
   - "sync" in text OR "Generating" in text => "generating"
   - "play_arrow" in text OR mat-icon:has-text('play_arrow') visible => "completed"
   - "error" in text => "failed"
6. Repeat polling until completed/failed
```

### Sequence 8: Get Audio Download URL via Play Interception (israelbls)

```
1. Locate item in artifact-library at index
2. Find play button: button[aria-label='Play']
3. Set up route handler: page.route("**/*", handler)
   - handler captures first request where request.resource_type == "media"
   - Aborts that request (preventing actual playback)
4. Click play button
5. Poll for captured_url (5-second timeout, 100ms intervals)
6. Unroute handler
7. Close audio player: button[aria-label='Close audio player']
8. Return captured URL
```

### Sequence 9: Download Audio via UI Menu (israelbls)

```
1. Locate item in artifact-library at index
2. Scroll into view: item.scroll_into_view_if_needed(timeout=2000)
3. Click More button: button[aria-label='More']
4. wait_for_timeout(500)
5. Click Download: page.get_by_role("menuitem", name="Download")
6. Reset download behavior via CDP:
   - cdp.send("Browser.setDownloadBehavior", {"behavior": "allow", "downloadPath": "/tmp/shared-downloads"})
7. Click Download menu item
8. Poll filesystem for new file (120s timeout, 500ms intervals)
   - Skip .crdownload and dotfiles
   - Wait 1s after detection for write completion
9. Read file bytes, return (content, name, size)
```

### Sequence 10: Download Audio via href (upamune -- Japanese UI)

```
1. Navigate to project URL
2. Get title: page.wait_for_selector("h1.notebook-title").inner_text()
3. Get summary: page.wait_for_selector("div.summary-content").inner_text()
4. Check for load button: button:has-text('読み込み')
   - If visible, click and wait for play button: button[aria-label='音声を再生'] timeout=120000
5. Wait for play button: button[aria-label='音声を再生'] timeout=30000
6. Click options: button[aria-label='オーディオ プレーヤーに関するその他のオプションを表示']
7. Find download link: a[mat-menu-item]:has-text('ダウンロード')
8. Extract href URL from download link
9. Get cookies from browser context: context.cookies()
10. Download via requests.Session with browser cookies
```

### Sequence 11: Clear All Sources (israelbls)

```
Loop (max 200 iterations):
  1. Get source items: div.single-source-container
  2. If count == 0, break
  3. Take first item
  4. Click more button:
     - Primary: button.source-item-more-button
     - Fallback: button:has(mat-icon:has-text('more_vert'))
  5. wait_for_timeout(200)
  6. Click: page.get_by_role("menuitem", name="Remove source")
  7. wait_for_timeout(200)
  8. Click confirm: page.get_by_role("button", name="Delete")
  9. Wait for item to detach: source.wait_for(state="detached", timeout=1000)
  10. wait_for_timeout(500)
```

### Sequence 12: Clear All Audio Artifacts (israelbls)

```
Loop (max 200 iterations):
  1. Get items: artifact-library > *
  2. If count == 0, break
  3. Take first item, scroll into view
  4. Click: button[aria-label='More']
  5. Click: page.get_by_role("menuitem", name="Delete")
  6. Click confirm: page.get_by_role("button", name="Delete")
  7. Wait for item detach: state="detached" timeout=2000
  8. wait_for_timeout(300)
```

### Sequence 13: Rename Notebook (DataNath)

```
1. Click title: page.locator(".title-input").click()
2. Select all: page.keyboard.press("Control+A")
3. Type new name: title_box.fill(notebook_name)
4. Confirm: title_box.press("Enter")
5. page.wait_for_timeout(1000)
```

---

## Selector Stability Summary

### STABLE (aria-labels and roles -- recommended)

These survive UI redesigns as long as functionality exists:

```python
page.get_by_role("button", name="Create new notebook")
page.get_by_role("button", name="Add source")
page.get_by_role("button", name="Insert")
page.get_by_role("button", name="Generate")
page.get_by_role("button", name="Delete")
page.get_by_role("menuitem", name="Remove source")
page.get_by_role("menuitem", name="Delete")
page.get_by_role("menuitem", name="Download")
button[aria-label='Play']            # en
button[aria-label='More']            # en
button[aria-label='Close audio player']  # en
```

### MODERATE (Angular form controls, custom elements)

Tied to Angular/Material framework but more stable than CSS classes:

```python
[formcontrolname='newUrl']           # URL input
textarea[formcontrolname='textInput']  # Text input
mat-dialog-container                 # Dialog detection
mat-dialog-actions                   # Dialog buttons area
mat-radio-button                     # Style selection
mat-select / mat-option              # Dropdowns
mat-chip-option                      # Source type chips
artifact-library                     # Audio artifacts container
```

### FRAGILE (CSS classes -- break on redesign)

These are implementation-specific and will break when Google updates the UI:

```python
span.mdc-evolution-chip__text-label  # Chip label text
.mat-progress-spinner               # Loading indicator
.mat-mdc-progress-spinner           # Loading indicator (newer)
div.single-source-container         # Source item wrapper
button.source-item-more-button      # Source context menu
.mdc-checkbox__native-control       # Source checkbox
mat-icon.edit-button-icon           # Edit pencil icon
.artifact-library-container         # Artifact item wrapper
h1.notebook-title                   # Notebook title
div.summary-content                 # Notebook summary
.title-input                        # Editable title field
```

---

## Key Differences Between Repos

| Feature | israelbls | upamune | DataNath |
|---------|-----------|---------|----------|
| **Auth** | CDP + auto-launch Chrome | CDP (user-launched) | storage_state (headless-capable) |
| **Headless** | No (needs visible Chrome) | No (needs visible Chrome) | Yes (after initial login) |
| **Create notebook** | No (works with existing) | Yes (first URL creates) | Yes (first URL creates) |
| **Add URL** | Yes (with dialog management) | Yes | Yes |
| **Add text** | Yes | No | No |
| **Add YouTube** | Yes (same as URL) | Yes (auto-detects from URL) | Yes (separate CSV) |
| **Generate audio** | Yes (style, prompt, language) | Yes (simple click) | No |
| **Poll status** | Yes (generating/completed/failed) | No | No |
| **Download audio** | Yes (2 methods: URL interception + UI download) | Yes (href extraction + requests) | No |
| **Clear sources** | Yes | No | No |
| **Clear audio** | Yes | No | No |
| **Rename notebook** | No | No | Yes |
| **Get share link** | No | No | No |
| **Language support** | en, he | en, ja | en only |
| **API** | FastAPI REST | CLI + Streamlit | CLI only |
| **Batch URLs** | Yes (grouped into one paste) | Yes (one-by-one) | Yes (one-by-one) |
| **Error handling** | Extensive fallback selectors | Basic try/catch | expect() assertions |
| **Spotify upload** | No | Yes | No |

---

## Notable Implementation Details

### URL Grouping (israelbls)
URLs and YouTube links are grouped together and pasted as a newline-separated string into a single text field, rather than adding one-by-one. This is faster but relies on NLM accepting multi-line URL input.

### Source Processing Wait (DataNath)
After inserting a URL, DataNath waits for the specific source container to appear, its spinner to finish, AND a checkbox to appear with a label different from the URL (indicating processing is complete). This is the most robust completion detection.

```python
source_container = page.locator("div.single-source-container").last
source_container.wait_for(state="attached")
loading_spinner = source_container.locator(".mat-mdc-progress-spinner")
loading_spinner.wait_for(state="detached")
checkbox = source_container.locator("input.mdc-checkbox__native-control.mdc-checkbox--selected")
checkbox.wait_for(state="attached")
expect(checkbox).not_to_have_attribute("ariaLabel", url)  # label changes when processed
```

### Audio URL Interception (israelbls)
Uses Playwright's `page.route()` to intercept the media request when the play button is clicked, capturing the CDN URL without actually playing the audio. This is clever but the URL may be short-lived (session-signed by Google).

### Download via CDP (israelbls)
Resets Chrome's download behavior via CDP session before clicking Download:
```python
cdp = self.page.context.new_cdp_session(self.page)
cdp.send("Browser.setDownloadBehavior", {
    "behavior": "allow",
    "downloadPath": "/tmp/shared-downloads"
})
```
Then monitors the filesystem for new files. This avoids Playwright's `expect_download()` which doesn't work well with Chrome's download mechanism for NLM audio.

### Jina Reader Prefix (upamune)
Optionally prepends `https://r.jina.ai/` to URLs before adding them as sources, using Jina's reader API for better content extraction. This is passed through to NLM as a "Website" source.

---

## Missing Operations (Not Found in Any Repo)

The following NLM operations are NOT automated by any of the three repos:

1. **Getting share/public links** -- none of the repos automate this
2. **Generating reports** (Briefing Doc, Study Guide, etc.) -- audio only
3. **Generating other artifacts** (mind maps, slides, flashcards, quizzes, etc.)
4. **Adding Google Drive sources**
5. **Adding file upload sources** (PDF, etc.)
6. **Querying the notebook** (chat)
7. **Managing notes**
8. **Research/web search within NLM**

For these operations, the `notebooklm-mcp-cli` (API-based, not browser automation) remains the only option.
