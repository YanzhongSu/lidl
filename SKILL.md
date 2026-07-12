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

## Scope

This skill is only for Lidl receipt export/parsing/querying. Do not conflate it with unrelated business metrics, email, FT/news, WhatsApp, or other app workflows just because they happened in the same session. If the user switches topics, load/use the skill for that new class of work instead.

## Workflow

For local analysis questions, use existing JSON files first. Only authenticate when an API refresh is needed.

Common fast paths:

- "What did I buy yesterday / in the past few days?" Run `query` against `./data/receipts_detail.json`; do not call the Lidl API unless the user asks for a refresh or the requested date range may not be present locally.
- "Have I bought anything since last time we checked?" Run `update` with the appropriate auth option once. It loads `./data/receipts_summaries.json`, finds the max date among `items`, fetches summary pages only until that checkpoint date is covered, fetches details only for new receipt ids, parses, prints the new receipts, then stops.
- "Should I refresh?" Run `status`. It prints current UTC time, max receipt date, and whether the max receipt date is older than `--refresh-after-hours` (default `6`).

### Fallback when refresh/auth is unavailable

For "since last time" questions, attempt one refresh path when appropriate, but if authentication is unavailable and the user does not provide a cookie/login within the current turn, still answer from the cached export instead of stopping with only an auth problem. Use `status` to identify both `fetched_at` and `max_receipt_date`, then query the cached range after the checkpoint if useful. Report clearly:

- last successful export time (`fetched_at`),
- latest cached receipt date (`max_receipt_date`),
- cached spend/receipt count since that checkpoint,
- and that live purchases after `fetched_at` are unverified until Lidl auth is refreshed.

Do not imply there were no real-world purchases after the cached export; say only that none are present in the local cached data.

Authentication decision (priority order):

**1. Playwright MCP / browser-session (PREFERRED)** — Use the connected Playwright MCP to access the user's real Chrome session:
   a. Navigate to `https://www.lidl.co.uk/mla/` via Playwright MCP
   b. Check if already logged in (page shows account greeting)
   c. **If logged in**: use `fetch(...)` from the page context with `credentials: 'include'` to call the Lidl receipt API. No separate login needed, no reCAPTCHA.
   d. **If not logged in**: login via Playwright MCP using browser_find/browser_type/browser_click with credentials from `~/.hermes/.env` (`$LIDL_USER`, `$LIDL_PW`). Because this runs in the user's real Chrome (not a temp profile), reCAPTCHA trusts the session. Then fetch receipts from the authenticated context.
   e. This is the single unified path — always try Playwright MCP first for both login and data fetching.

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

## Commands

Use `scripts/lidl_receipts.py`:

⚠️ **Argparse order matters**: `--cookie-stdin`, `--login`, and other global flags must come BEFORE the subcommand (e.g. `--cookie-stdin all`, not `all --cookie-stdin`). The script uses argparse subparsers — global options on the main parser won't be recognised after the subcommand name.

VM/headless or remote-agent mode:

```bash
python3 scripts/lidl_receipts.py --cookie-stdin all
```

Local interactive-browser mode:

```bash
# Current helper versions may not expose an auth-check subcommand.
# If an existing browser/auth state is valid, update directly:
python3 scripts/lidl_receipts.py --login --auth-browser-channel chrome update --include-articles

# If interactive auth is required, run the desired API command with interactive login flags
# and ask the user to complete the login in the opened browser:
python3 scripts/lidl_receipts.py --login --auth-interactive --auth-browser-channel chrome update --include-articles
```

Useful subcommands:

```bash
python3 scripts/lidl_receipts.py [AUTH_OPTION] auth-check
python3 scripts/lidl_receipts.py [AUTH_OPTION] summaries
python3 scripts/lidl_receipts.py [AUTH_OPTION] update --include-articles
python3 scripts/lidl_receipts.py [AUTH_OPTION] summaries-since
python3 scripts/lidl_receipts.py [AUTH_OPTION] details
python3 scripts/lidl_receipts.py parse
python3 scripts/lidl_receipts.py status
python3 scripts/lidl_receipts.py query --start 2026-05-09 --end 2026-05-10 --include-articles
python3 scripts/lidl_receipts.py query --days 3 --include-articles
```

Use `[AUTH_OPTION]` as one of:

- `--cookie-stdin` when the user supplies the full Cookie header through stdin (reads a single line).
- no explicit option when `LIDL_COOKIE` is set in the environment.
- `--login` only on a machine with interactive browser UI or an existing valid `./data/lidl_auth_state.json`.

Default options:

- Data directory: `./data`
- Country: `GB`
- Language code: `en-GB`
- Rate limit: `3` requests/second
- Summary endpoint: `https://www.lidl.co.uk/mre/api/v1/tickets?country=GB&page={page}`
- Detail endpoint: `https://www.lidl.co.uk/mre/api/v1/tickets/{id}?country=GB&languageCode=en-GB`

Use `--data-dir`, `--country`, `--language-code`, or `--rate` only when the user asks or local context requires it.

## Browser-session API fallback (PREFERRED PATH)

Use this whenever Playwright MCP is connected to the user's running Chrome session. This is the **primary** auth path for this skill because:

- It uses the user's **real Chrome** — no temp profile, no reCAPTCHA flagging
- The user keeps Lidl logged in day-to-day, so often no login needed
- If login is needed, credential submission happens in a real browser that reCAPTCHA trusts
- Saved credentials from `~/.hermes/.env` (`LIDL_USER`, `LIDL_PW`) can be used for MCP-driven form fills

### Workflow

1. **Navigate to the Lidl account page via Playwright MCP:**

```text
https://www.lidl.co.uk/mla/?country_code=gb&language=en-GB&client_id=GreatBritainRetailClient
```

2. **Check if already logged in.** If the page shows an account greeting (e.g. "My account", order history, etc.), skip to step 4. If redirected to `accounts.lidl.com/Account/Login`, proceed to step 3.

3. **Login via Playwright MCP (only if redirected to login page).** Use `browser_find` and `browser_type` / `browser_click` on the MCP-connected page to fill credentials from the environment:

   - Email field: locate `[data-testid="input-email"]` or the email textbox → type `$LIDL_USER` → click the "Next" button
   - Password field: locate `[data-testid="login-input-password"]` → type `$LIDL_PW` → click the login button
   - Wait for redirect back to `www.lidl.co.uk/mla/`

   Because these interactions go through the user's real Chrome (Playwright MCP Chrome extension mode), reCAPTCHA sees a legitimate browser session and should accept the login.

4. **Find the local checkpoint** from existing summaries:

```bash
python3 scripts/lidl_receipts.py --data-dir /Users/yanzhongsu/data status
```

5. **From the authenticated page context** (still on `www.lidl.co.uk`), fetch new summaries and details with the browser console:

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

6. **Save results locally:** Save each `details[id]` to `./data/receipts/{id}.json`, prepend/merge `newSummaries` into `./data/receipts_summaries.json` without duplicating ids, update `./data/receipts/_manifest.json`, then run:

```bash
python3 scripts/lidl_receipts.py --data-dir /Users/yanzhongsu/data parse
python3 scripts/lidl_receipts.py --data-dir /Users/yanzhongsu/data status
```

7. **Report the new receipts only.** Never print or store cookies, `authToken`, `customer-info`, `ldi-session-info`, or other browser credential values.

## Efficient Query Recipes

For "since last time we checked":

```bash
python3 scripts/lidl_receipts.py update [AUTH_OPTION] --include-articles
```

If you only need to know whether a refresh is likely needed:

```bash
python3 scripts/lidl_receipts.py status
```

For date-range questions, calculate explicit date boundaries. `--start` is inclusive and `--end` is exclusive:

```bash
python3 scripts/lidl_receipts.py query --start 2026-05-09 --end 2026-05-10 --include-articles
```

## Output Contract

Parsed output contains `receipts[]` entries with: `id`, `date`, `store_name`, `total_displayed`, `articles_total`, `discounts_total`, `computed_net_total`, `payment_method`, `card_last4`, `vat_breakdown`, `article_count`, `discount_count`, `articles[]`, and `discounts[]`.

### Computed vs displayed totals

Prefer the **displayed total** (`total_displayed`) from the HTML over the computed total (`computed_net_total`), because multi-buy proration can cause articles-sum-plus-discounts to slightly overstate the actual charge. The displayed total matches what was actually paid. Use `computed_net_total` as a sanity check — should be within ~10% of displayed total.

## HTML Parsing reference

See `references/api-response-format.md` for the full API response structure.

Key patterns for the Lidl UK receipt HTML:

- **Store name**: `<span id="header_line_1">Shepherds Bush</span>` — inner text. Fall back to `ticket.store.name` from the API response.
- **Date**: `<span id="purchase_tender_information_3">Date: 23/07/25 Time: 20:48:14</span>` in DD/MM/YY. Normalise to ISO.
- **Total**: `<span id="purchase_summary_2" class="css_bold">28.73</span>` — first css_bold in purchase_summary.
- **Payment**: `data-tender-description="CARD"` attribute on purchase_summary_3 span.
- **Card last 4**: Masked pattern `***********0615`.
- **Articles**: `<span class="article" data-art-id="..." data-unit-price="..." data-art-description="...">`. Always use `data-art-*` attributes, not display text. Skip weight-continuation rows (same data-art-id, no data-unit-price).
- **Discounts**: Spans with `class="discount"`. Group by `purchase_list_line_N` id (NOT by `data-promotion-id`, which repeats across line items). Each group has one name span and one amount span.
- **VAT**: `<span id="vat_info_line_2" data-tax-type="..." data-tax-percentage="..." data-tax-base-amount="..." data-tax-amount="...">`.

### Price parsing pitfalls

Never use "strip all non-digit characters" to parse prices. Discount name text like "£5 off £35 spend" would become `535`. Use a strict regex: optional currency prefix, optional minus, digits, optional dot+digits — reject anything with letters.

## Failure Handling

- If an API call returns `401` or `403`, refresh the chosen auth method. On VM/headless runs, ask for a fresh copied Cookie header. On local interactive-browser runs, rerun `auth-check --login --auth-interactive`.
- Lidl uses **Google reCAPTCHA Enterprise** (invisible v3) and **FingerprintJS** on the login page. Automated credential login without `--auth-interactive` will almost certainly be rejected by reCAPTCHA — this is expected. Use `--auth-interactive` once to complete login manually, or use the browser-session API fallback for automated refreshes.
- **Corrupted auth state file**: If `lidl_auth_state.json` is 1MB+ or contains cookies from dozens of unrelated sites (e.g. copied from a general browser cookie dump), Playwright fails to load it and crashes with `TargetClosedError: Target page, context or browser has been closed` on `page.goto()`. Fix: pass `--no-auth-state` to skip the corrupted file and force a fresh headed login.
- For agent credentials, the script supports `LIDL_USER`/`LIDL_EMAIL` for email and `LIDL_PW`/`LIDL_PASSWORD` for password (set in `~/.hermes/.env`).
- If `--login ... update` fails before opening a browser with `Missing Lidl email. Provide --email or set LIDL_EMAIL`, do not stop or ask for credentials if Playwright MCP can access the user's logged-in Chrome session. Treat it as a signal to switch to the browser-session API fallback: verify the `/mla/` page shows the account greeting, then fetch summaries/details via page-context `fetch(..., {credentials: 'include'})`.
- If using the browser-session API fallback, ensure the account page is actually logged in first, call summary `page=1` not `page=0`, and fetch details from the same page context with `credentials: 'include'`. Save the browser evaluation result to a local JSON file only if needed for merging; never include cookies/tokens in that file or final answer.
- After browser-session fallback fetching, merge new summaries by id, write each detail under `data/receipts/{id}.json`, run `parse`, then run `status` and a query/filter for the checkpoint range to verify receipt count and total spend before reporting.
- If detail fetching stops partway through, rerun `details` or `all`; existing receipt files are skipped.
- If parsing reports missing HTML receipts, check both `ticket.htmlPrintedReceipt` / `ticket.htmlReceipt` and top-level `htmlPrintedReceipt` / `htmlReceipt` before concluding the detail is missing. Current Lidl API responses commonly nest receipt HTML under `ticket`.
- If copied-cookie commands error with `unrecognized arguments: --cookie-stdin`, move global options before the subcommand (for example `python3 scripts/lidl_receipts.py --cookie-stdin all`).
- Keep credentials, cookies, tokens, and auth state out of commits and final answers.

## Maintenance and upstream sync

When you change the installed helper script or parser behavior during a task, do not leave the skill copy and upstream repo diverged. Sync fixes back to the source repo (`YanzhongSu/lidl`) when the user asks or when the fix is clearly reusable:

```bash
# use the user's SSH GitHub workflow
cd /tmp/lidl  # or clone git@github.com:YanzhongSu/lidl.git
make smoke
python3 scripts/lidl_receipts.py parse --data-dir /Users/yanzhongsu/data
python3 scripts/lidl_receipts.py query --data-dir /Users/yanzhongsu/data --days 7
git add Makefile scripts/lidl_receipts.py tests/
git commit -m "fix: support nested Lidl receipt responses"
git remote set-url origin git@github.com:YanzhongSu/lidl.git
git push origin main
```

Use `make smoke` plus a parse/query against real cached receipts as verification. Regression tests should cover nested `ticket.htmlPrintedReceipt`, top-level legacy receipt shapes, discount labels such as `£5 off £35 spend`, weighted-product continuation rows, and displayed-vs-computed total reconciliation.

## Reference Notes

- `references/parser-and-cli-gotchas.md` captures verified parser pitfalls from real Lidl UK receipt data: nested `ticket` responses, discount labels such as `£5 off £35 spend`, weighted-product continuation rows, total reconciliation, and CLI option ordering.
- `references/lidl-anti-bot-analysis.md` documents the reCAPTCHA Enterprise v3, FingerprintJS, and behavioral worker found on `accounts.lidl.com`, plus the `--no-auth-state` workaround for corrupted auth state files.
