---
name: lidl
description: Fetch, resume, and parse Lidl UK digital receipts from lidl.co.uk purchase-history API responses. Use when a user asks to download Lidl receipt summaries, fetch receipt JSON details, parse htmlPrintedReceipt into structured articles/discounts/VAT/payment data, or create/update local Lidl receipt export files under ./data.
---

# Lidl

## Overview

Use this skill to export a user's Lidl UK receipt history into deterministic local JSON files:

- `./data/receipts_summaries.json` for paginated receipt summaries.
- `./data/receipts/{id}.json` for each raw receipt detail response.
- `./data/receipts_detail.json` for parsed receipt, article, discount, VAT, payment, and spend data.

Use the authentication method that fits the runtime. If the agent is definitely running on a machine with an interactive browser UI, Playwright browser auth state can avoid repeated cookie pasting. If the agent has access to the user's already logged-in Chrome session through Playwright MCP / computer-use, the fastest path is often to call the Lidl receipt API from that logged-in page context and then save only aggregate/raw receipt JSON locally. If the agent is running on a VM, container, remote worker, CI job, or any environment without browser UI, copied-cookie mode is the correct path. Do not hardcode or commit credentials, cookies, tokens, or auth state.

## Workflow

For local analysis questions, use existing JSON files first. Only authenticate when an API refresh is needed.

Common fast paths:

- "What did I buy yesterday / in the past few days?" Run `query` against `./data/receipts_detail.json`; do not call the Lidl API unless the user asks for a refresh or the requested date range may not be present locally.
- "Have I bought anything since last time we checked?" or "Show me what I bought since last time we checked" Run `update` with the appropriate auth option once. It loads `./data/receipts_summaries.json`, finds the max date among `items`, fetches summary pages only until that checkpoint date is covered, fetches details only for new receipt ids, parses, prints the new receipts, then stops.
- "Should I refresh?" Run `status`. It prints current UTC time, max receipt date, and whether the max receipt date is older than `--refresh-after-hours` (default `6`).

Authentication decision (priority order):

**1. Playwright MCP / browser-session (PREFERRED)** — If Playwright MCP is connected to the user's running Chrome session:
   a. Navigate to `https://www.lidl.co.uk/mla/` via Playwright MCP
   b. Check the page shows the account greeting (already logged in — user keeps Lidl logged in day-to-day)
   c. If logged in: use `fetch(...)` from the page context with `credentials: 'include'` to call the Lidl receipt API. This reuses the user's real browser session — no separate login needed, no reCAPTCHA, no temp profiles.
   d. If not logged in: fall through to option 2.

**2. Copied cookie** — If the user has a Lidl session open in their browser and can copy the `Cookie` header from a receipt API request, use `--cookie-stdin` or `LIDL_COOKIE` env var. This is the right path for VM/headless/remote agent environments with no browser UI.

**3. Interactive Playwright login (`--login --auth-interactive`)** — On a machine with a display, use `--login --auth-interactive` which opens a headed Chrome window for the user to complete Lidl login manually. After success, saves `lidl_auth_state.json` for reuse on subsequent runs until cookies expire.

**4. Automated credential login (`--login` without `--auth-interactive`)** — Attempts fully automated login using `LIDL_USER`/`LIDL_EMAIL` and `LIDL_PW`/`LIDL_PASSWORD` from the environment. The script launches headed real Chrome with anti-bot mitigations: `navigator.webdriver` hidden, `press_sequentially()` for human-like typing, realistic viewport/user-agent, and random delays. **However, Lidl uses reCAPTCHA Enterprise + FingerprintJS** on the login page, so automated login will almost certainly be rejected. This is expected and not a bug. If it fails, fall back to option 3 or 1.

**5. No auth / cached data only** — If none of the above are available, answer from `./data/receipts_detail.json` and clearly report the last fetch time.

⚠️ **Corrupted auth state file**: If `lidl_auth_state.json` is 1MB+ or was dumped from a general browser cookie store (contains cookies from dozens of unrelated sites), Playwright crashes with `TargetClosedError` when trying to load it. Fix: pass `--no-auth-state` to skip the corrupted file, or delete it to regenerate a clean one via `--login --auth-interactive`.

Full export workflow:

1. Choose the auth method using the priority order above.
2. For Playwright MCP path: use the browser-session API fallback (detailed below).
3. For VM/headless runs: get a fresh full `Cookie` header from the user and pass via `--cookie-stdin` or `LIDL_COOKIE`.
4. For local interactive-browser runs: use `--login --auth-interactive` once, then `--login` reuses the saved state.
5. Fetch summaries first. The script reads `totalCount` and page `size` to request every summary page.
6. Fetch detail JSON next. Existing `./data/receipts/{id}.json` files are skipped, so interrupted runs resume.
7. Parse the saved raw details into `./data/receipts_detail.json`.

Authentication notes

- Lidl UK uses an OpenID Connect authorization-code flow with PKCE through `accounts.lidl.com`.
- Successful login redirects back to `www.lidl.co.uk/user-api/signin-oidc`, which sets first-party cookies used by `/mre/api/v1/tickets`.
- Relevant post-login receipt cookies include `ldi-user-context`, `authToken`, `ldi-session-info`, `ldi-customertoken`, `tracking-info`, and `customer-info`.
- **The best path for automated refresh**: use Playwright MCP to navigate to `/mla/` in the user's already-logged-in Chrome, then run `fetch(...)` from that page context with `credentials: 'include'`. No reCAPTCHA, no credential handling, no temp browser profile. The user's existing Lidl session cookies are automatically included.
- For agent credentials in standalone runs, the script supports `LIDL_USER`/`LIDL_EMAIL` for email and `LIDL_PW`/`LIDL_PASSWORD` for password (set in `~/.hermes/.env`). These are used by `resolve_login_credentials()`.

## Commands

Use `scripts/lidl_receipts.py`:

VM/headless or remote-agent mode:

```bash
python3 scripts/lidl_receipts.py all --cookie-stdin
```

Local interactive-browser mode:

```bash
python3 scripts/lidl_receipts.py auth-check --login --auth-interactive --auth-browser-channel chrome
python3 scripts/lidl_receipts.py all --login
```

Useful subcommands:

```bash
python3 scripts/lidl_receipts.py auth-check [AUTH_OPTION]
python3 scripts/lidl_receipts.py summaries [AUTH_OPTION]
python3 scripts/lidl_receipts.py update [AUTH_OPTION] --include-articles
python3 scripts/lidl_receipts.py summaries-since [AUTH_OPTION]
python3 scripts/lidl_receipts.py details [AUTH_OPTION]
python3 scripts/lidl_receipts.py parse
python3 scripts/lidl_receipts.py status
python3 scripts/lidl_receipts.py query --start 2026-05-07 --end 2026-05-08 --include-articles
python3 scripts/lidl_receipts.py query --days 3 --include-articles
```

Use `[AUTH_OPTION]` as one of:

- `--cookie-stdin` when the user supplies the full Cookie header through stdin.
- no explicit option when `LIDL_COOKIE` is set in the environment.
- `--login` only on a machine with interactive browser UI or an existing valid `./data/lidl_auth_state.json`.

When an agent already has the cookie in conversation context, prefer stdin to avoid putting the cookie on the process command line:

```bash
python3 scripts/lidl_receipts.py all --cookie-stdin
```

Default options:

- Data directory: `./data`
- Country: `GB`
- Language code: `en-GB`
- Rate limit: `3` requests/second
- Summary endpoint: `https://www.lidl.co.uk/mre/api/v1/tickets?country=GB&page={page}`
- Detail endpoint: `https://www.lidl.co.uk/mre/api/v1/tickets/{id}?country=GB&languageCode=en-GB`

Use `--data-dir`, `--country`, `--language-code`, or `--rate` only when the user asks or local context requires it.

Use `--insecure` only when the local Python TLS trust store rejects the connection with a certificate-chain error in a controlled environment.

## Browser-session API fallback

Use this when Playwright MCP / computer-use can access the user's logged-in Chrome session and the helper script's `--login` path cannot bootstrap auth quickly. This avoids reading or typing saved passwords.

1. Navigate to the Lidl account page and verify it is already logged in:

```text
https://www.lidl.co.uk/mla/?country_code=gb&language=en-GB&client_id=GreatBritainRetailClient
```

2. Find the local checkpoint from existing summaries:

```bash
python3 scripts/lidl_receipts.py --data-dir /Users/yanzhongsu/data status
```

3. From the authenticated page context, fetch new summaries and details with `fetch(..., {credentials: 'include'})`. Lidl summary pages are 1-indexed; `page=0` returns `400`, while `page=1` returns the newest receipts.

```js
async () => {
  const cutoff = new Date('CHECKPOINT_ISO_Z');
  const sr = await fetch('https://www.lidl.co.uk/mre/api/v1/tickets?country=GB&page=1', {credentials: 'include'});
  const summariesPayload = await sr.json();
  const newSummaries = summariesPayload.items.filter(x => new Date(x.date) > cutoff);
  const details = {};
  for (const s of newSummaries) {
    const r = await fetch(`https://www.lidl.co.uk/mre/api/v1/tickets/${s.id}?country=GB&languageCode=en-GB`, {credentials: 'include'});
    details[s.id] = await r.json();
  }
  return {fetched_at: new Date().toISOString().replace(/\.\d{3}Z$/, 'Z'), summariesPayload, newSummaries, details};
}
```

4. Save each `details[id]` to `./data/receipts/{id}.json`, prepend/merge `newSummaries` into `./data/receipts_summaries.json` without duplicating ids, update `./data/receipts/_manifest.json`, then run:

```bash
python3 scripts/lidl_receipts.py --data-dir /Users/yanzhongsu/data parse
python3 scripts/lidl_receipts.py --data-dir /Users/yanzhongsu/data status
```

5. Report the new receipts only. Never print or store cookies, `authToken`, `customer-info`, `ldi-session-info`, or other browser credential values.

## Efficient Query Recipes

For "since last time we checked":

```bash
python3 scripts/lidl_receipts.py update [AUTH_OPTION] --include-articles
```

If you only need to know whether a refresh is likely needed:

```bash
python3 scripts/lidl_receipts.py status
```

For date-range questions, calculate explicit date boundaries and use `query`. `--start` is inclusive and `--end` is exclusive:

```bash
python3 scripts/lidl_receipts.py query --start 2026-05-09 --end 2026-05-10 --include-articles
```

## Output Contract

The parsed output should contain:

- `parsed_at`, `total_receipts`, `total_articles`, `total_discounts`, `total_spent`
- `receipts[]` entries with `id`, `date`, store fields, `total_amount`, `payment_method`, `card_last4`, `vat_breakdown`, `loyalty_points`, `articles`, `discounts`, `article_count`, and `discount_count`

Parsing notes:

- Parse article rows from `<span class="article">` elements and skip weight continuation rows whose visible text starts with whitespace.
- Parse discounts sequentially from `<span class="discount css_bold">` rows instead of grouping only by promotion id.
- Prefer computed totals from article line totals plus discounts when close to the displayed total, because some Lidl HTML total spans truncate.
- Extract payment method from `data-tender-description` and card last 4 from masked card patterns such as `***********0615`.
- Extract VAT from `data-tax-type`, `data-tax-percentage`, `data-tax-base-amount`, and `data-tax-amount`.

## Failure Handling

- If an API call returns `401` or `403`, refresh the chosen auth method. On VM/headless runs, ask for a fresh copied Cookie header. On local interactive-browser runs, rerun `auth-check --login --auth-interactive`.
- If using the browser-session API fallback, ensure the account page is actually logged in first, call summary `page=1` not `page=0`, and fetch details from the same page context with `credentials: 'include'`.
- If detail fetching stops partway through, rerun `details` or `all`; existing receipt files are skipped.
- If parsing reports missing HTML receipts, keep the raw JSON files and summarize the affected receipt ids.
- Keep credentials, cookies, tokens, and auth state out of commits and final answers.
