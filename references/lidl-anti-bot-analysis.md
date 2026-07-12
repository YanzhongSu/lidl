# Lidl Anti-Bot Analysis

## Detected Defenses (July 2026)

Confirmed via Playwright MCP on the Lidl login page (`accounts.lidl.com`):

### 1. Google reCAPTCHA Enterprise (invisible v3)
- **Script**: `recaptcha-9IdXwfru.js` loaded from `accounts.lidl.com/themes/omnichannelregistration/assets/`
- **CSS**: `recaptcha-C6_MWHjV.css`
- **Enterprise key**: `6LdL-lsaAAAAABo0b2M_cFQbrX0Btbo85uZdRXWS` (visible in network requests)
- **Mechanism**: Invisible v3 — no checkbox challenge. Generates a score (0.0–1.0) based on user interaction patterns. Low scores are silently rejected server-side.
- **What triggers low score**: Playwright action patterns, lack of real mouse movement, programmatic form interactions, headless browser signatures.

### 2. FingerprintJS (`fpapi.io` / `fpjs.io`)
- Present in the Content-Security-Policy as allowed script sources.
- Collects browser fingerprint: canvas, WebGL, fonts, audio, WebRTC, etc.
- Cross-references Playwright's spoofed fingerprint against known automation patterns.

### 3. Behavioral Worker (blocked by CSP)
- A `blob:` worker was attempted but blocked by CSP with:
  ```
  Creating a worker from 'blob:https://accounts.lidl.com/...' violates CSP
  default-src ... *.fpapi.io *.fpjs.io ...
  ```
- This worker likely analyzes mouse/touch/scroll behavior in real-time.

### 4. Cookie consent banner
- Appears on first visit. Not a bot-check but must be dismissed for clean flow.

## Why Automated Playwright Login Fails

Even with all anti-detection mitigations (headed mode, real Chrome channel, `navigator.webdriver` hidden, `press_sequentially()`, random delays, realistic viewport/UA), the reCAPTCHA Enterprise v3 score is too low for the server to accept the login form submission. The page simply reloads to the login form with no error message.

## Working Approaches

| Approach | How | Trust Level |
|----------|-----|-------------|
| **Browser-session API fallback** | Use Playwright MCP on already-logged-in Chrome → `fetch(..., {credentials:'include'})` from page context | Best — no login needed |
| **`--auth-interactive`** | Script opens Chrome, user completes captcha/login manually once → saves auth state | Good — only needs one manual interaction per session expiry |
| **Cookie paste** | User copies full Cookie header from logged-in browser → pass via `LIDL_COOKIE` or `--cookie-stdin` | Works — but cookies expire |
| **Automated `--login`** | Script attempts credential login with anti-bot mitigations | Sometimes works — headed Chrome + press_sequentially can pass reCAPTCHA |

## Tested Anti-Bot Mitigations

The following mitigations are applied by `login_with_browser()` and collectively can bypass Lidl's reCAPTCHA in some sessions:

- `page.add_init_script()` to set `navigator.webdriver = undefined`
- `press_sequentially()` with 30-80ms delays (instead of `fill()`)
- Random sleeps (300-2500ms) between actions
- Headed mode with `channel="chrome"` (real Chrome, not Playwright Chromium)
- Realistic viewport (1440×900)
- Realistic user-agent (Chrome 126 on macOS)
- `navigator.plugins` and `navigator.languages` override

### Known failure: corrupted auth state file

If `lidl_auth_state.json` was created by dumping cookies from a multi-site browser session (rather than a fresh Playwright `context.storage_state()`), it can be 1MB+ with hundreds of junk cookies. Playwright's `storage_state=str(path)` then fails silently, producing `TargetClosedError: Target page, context or browser has been closed` on the subsequent `page.goto()`.

**Fix**: pass `--no-auth-state` to skip the corrupted file and force a fresh headed-login flow.
