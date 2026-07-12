#!/usr/bin/env python3
"""Fetch and parse Lidl UK digital receipts."""

from __future__ import annotations

import argparse
import glob
import getpass
import html
import json
import math
import os
import re
import sys
import time
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SUMMARY_URL = "https://www.lidl.co.uk/mre/api/v1/tickets"
DETAIL_URL = "https://www.lidl.co.uk/mre/api/v1/tickets/{ticket_id}"
LIDL_HOME_URL = "https://www.lidl.co.uk/mla/"
DEFAULT_AUTH_STATE_FILENAME = "lidl_auth_state.json"


class RateLimiter:
    def __init__(self, requests_per_second: float) -> None:
        self.min_interval = 1.0 / requests_per_second if requests_per_second > 0 else 0.0
        self.last_request = 0.0

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        now = time.monotonic()
        sleep_for = self.min_interval - (now - self.last_request)
        if sleep_for > 0:
            time.sleep(sleep_for)
        self.last_request = time.monotonic()


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp_path.replace(path)


def print_err(message: str) -> None:
    print(message, file=sys.stderr)


def auth_state_path(args: argparse.Namespace) -> Path | None:
    if args.no_auth_state:
        return None
    if args.auth_state:
        return args.auth_state.expanduser().resolve()
    return args.data_dir / DEFAULT_AUTH_STATE_FILENAME


def cookie_header_from_cookies(cookies: list[dict[str, Any]], request_path: str = "/mre/api/v1/tickets") -> str:
    now = time.time()
    pairs = []
    for cookie in cookies:
        name = cookie.get("name")
        value = cookie.get("value")
        if not name or value is None:
            continue

        domain = str(cookie.get("domain") or "").lstrip(".")
        if domain and domain != "www.lidl.co.uk" and not "www.lidl.co.uk".endswith(f".{domain}"):
            continue

        path = str(cookie.get("path") or "/")
        if not request_path.startswith(path.rstrip("/") or "/"):
            continue

        expires = float(cookie.get("expires") or -1)
        if expires > 0 and expires < now:
            continue

        pairs.append(f"{name}={value}")

    return "; ".join(pairs)


def cookie_header_from_auth_state(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        state = read_json(path)
    except Exception as exc:  # noqa: BLE001 - explain the bad cache and continue to other auth sources
        print_err(f"Ignoring unreadable Lidl auth state {path}: {exc}")
        return None
    cookie = cookie_header_from_cookies(list(state.get("cookies", [])))
    return cookie or None


def validate_cookie(args: argparse.Namespace, cookie: str) -> bool:
    try:
        get_json(
            make_headers(cookie),
            SUMMARY_URL,
            {"country": args.country, "page": 1},
            RateLimiter(0),
            args.insecure,
        )
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, ssl.SSLError):
            raise SystemExit(
                "TLS certificate verification failed while validating Lidl auth. "
                "Use --insecure only if this is a controlled local trust-store issue."
            ) from None
        return False
    except Exception:
        return False
    return True


def resolve_login_credentials(args: argparse.Namespace) -> tuple[str, str]:
    email = args.email or os.environ.get("LIDL_EMAIL") or os.environ.get("LIDL_USER")
    password = (
        os.environ.get("LIDL_PASSWORD")
        or os.environ.get("LIDL_PW")
    )

    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\n")

    if not email:
        if sys.stdin.isatty():
            email = input("Lidl email: ").strip()
        else:
            raise SystemExit(
                "Missing Lidl email. Provide --email, set LIDL_EMAIL, or set LIDL_USER."
            )
    if not password:
        if sys.stdin.isatty():
            password = getpass.getpass("Lidl password: ")
        else:
            raise SystemExit(
                "Missing Lidl password. Set LIDL_PASSWORD, set LIDL_PW, or pass --password-stdin."
            )

    return email, password


def login_with_browser(args: argparse.Namespace) -> str:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit(
            "Credential login requires Playwright for Python. Install it with "
            "`python3 -m pip install playwright` and `python3 -m playwright install chromium`."
        ) from exc

    import random

    state_path = auth_state_path(args)
    # Use headed mode + real Chrome channel for credential login — Lidl detects
    # headless Chromium automation. When --auth-interactive is set the user
    # completes login manually anyway. When credentials are used automatically
    # we still launch headed so Playwright's automation signatures are less
    # obvious (full Chrome vs minimal Chromium).
    headed = True if (args.auth_headed or args.login) else args.auth_interactive
    launch_kwargs: dict[str, Any] = {"headless": not headed}
    if args.auth_browser_channel or (args.login and not args.auth_browser_channel):
        launch_kwargs["channel"] = args.auth_browser_channel or "chrome"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**launch_kwargs)

        if state_path and state_path.exists():
            context = browser.new_context(locale="en-GB", storage_state=str(state_path))
            page = context.new_page()
            page.goto(LIDL_HOME_URL, wait_until="domcontentloaded", timeout=args.auth_timeout * 1000)
            try:
                page.wait_for_load_state("networkidle", timeout=10_000)
            except PlaywrightTimeoutError:
                pass
            cookie = cookie_header_from_cookies(context.cookies(["https://www.lidl.co.uk"]))
            if cookie and validate_cookie(args, cookie):
                browser.close()
                print_err(f"Using saved Lidl browser auth state from {state_path}.")
                return cookie
            context.close()
            print_err("Saved Lidl browser auth state is missing or expired; logging in again.")

        context = browser.new_context(
            locale="en-GB",
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        # Hide Playwright/automation fingerprints that Lidl's anti-bot checks for
        page.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-GB', 'en']});
            // Override chrome.runtime if exposed by Playwright
            if (window.chrome && window.chrome.runtime) {
                Object.defineProperty(window.chrome, 'runtime', {get: () => undefined});
            }
            """
        )

        page.goto(LIDL_HOME_URL, wait_until="domcontentloaded", timeout=args.auth_timeout * 1000)

        if args.auth_interactive:
            print_err(
                "Complete the Lidl login in the opened browser window. "
                "The script will continue after the browser reaches www.lidl.co.uk/mla/."
            )
        elif "accounts.lidl.com" in page.url:
            email, password = resolve_login_credentials(args)
            page.wait_for_timeout(random.randint(800, 2000))

            email_input = page.locator('[data-testid="input-email"], #input-email').first
            email_input.wait_for(state="visible", timeout=args.auth_timeout * 1000)
            page.wait_for_timeout(random.randint(300, 800))
            # Use press_sequentially to simulate human typing (bypasses fill detection)
            email_input.press_sequentially(email, delay=random.randint(30, 80))
            page.wait_for_timeout(random.randint(500, 1200))
            page.locator('[data-testid="login-or-register-submit-button"]').click(timeout=15_000)

            page.wait_for_timeout(random.randint(1000, 2500))
            password_input = page.locator('[data-testid="login-input-password"], #Password').first
            password_input.wait_for(state="visible", timeout=args.auth_timeout * 1000)
            page.wait_for_timeout(random.randint(300, 800))
            password_input.press_sequentially(password, delay=random.randint(30, 80))
            page.wait_for_timeout(random.randint(500, 1200))
            page.locator('[data-testid="button-primary"]').click(timeout=15_000)

        try:
            page.wait_for_url("https://www.lidl.co.uk/mla/**", timeout=args.auth_timeout * 1000)
        except PlaywrightTimeoutError:
            pass
        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeoutError:
            pass

        cookie = cookie_header_from_cookies(context.cookies(["https://www.lidl.co.uk"]))
        if not cookie or not validate_cookie(args, cookie):
            current_url = page.url
            browser.close()
            raise SystemExit(
                "Lidl browser login did not produce an authenticated receipt session. "
                f"Current page: {current_url}. If Lidl shows a bot check or MFA challenge, rerun with "
                "--auth-interactive and complete the login manually once."
            )

        if state_path:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=str(state_path))
            print_err(f"Saved Lidl browser auth state to {state_path}.")

        browser.close()
        return cookie


def require_cookie(args: argparse.Namespace) -> str:
    cached_cookie = getattr(args, "_cookie_value", None)
    if cached_cookie:
        return cached_cookie
    if args.cookie_stdin:
        cookie = sys.stdin.read().strip()
    else:
        cookie = args.cookie or os.environ.get("LIDL_COOKIE")
    if not cookie and args.login:
        cookie = login_with_browser(args)
    if not cookie:
        state_path = auth_state_path(args)
        if state_path:
            cookie = cookie_header_from_auth_state(state_path)
    if not cookie:
        raise SystemExit(
            "Missing Lidl authentication. Provide --cookie, set LIDL_COOKIE, or use --login with "
            "LIDL_USER/LIDL_EMAIL and LIDL_PW/LIDL_PASSWORD."
        )
    setattr(args, "_cookie_value", cookie)
    return cookie


def make_headers(cookie: str) -> dict[str, str]:
    return {
        "accept": "application/json",
        "accept-language": "en-GB,en;q=0.9",
        "referer": "https://www.lidl.co.uk/mre/purchase-history",
        "user-agent": "Mozilla/5.0",
        "cookie": cookie,
    }


def get_json(
    headers: dict[str, str],
    url: str,
    params: dict[str, str | int],
    limiter: RateLimiter,
    insecure: bool = False,
) -> Any:
    limiter.wait()
    encoded_params = urllib.parse.urlencode(params)
    request = urllib.request.Request(f"{url}?{encoded_params}", headers=headers, method="GET")
    context = ssl._create_unverified_context() if insecure else None
    try:
        with urllib.request.urlopen(request, timeout=30, context=context) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code in {401, 403}:
            raise RuntimeError(f"HTTP {exc.code}: Lidl session cookie is expired or unauthorized") from exc
        raise RuntimeError(f"HTTP {exc.code}: {body[:200]}") from exc
    return json.loads(body)


def fetch_summaries(args: argparse.Namespace) -> dict[str, Any]:
    cookie = require_cookie(args)
    headers = make_headers(cookie)
    limiter = RateLimiter(args.rate)
    output_path = args.data_dir / "receipts_summaries.json"

    first_page = get_json(headers, SUMMARY_URL, {"country": args.country, "page": 1}, limiter, args.insecure)
    size = int(first_page.get("size") or len(first_page.get("items", [])) or 10)
    total_count = int(first_page.get("totalCount") or len(first_page.get("items", [])))
    total_pages = max(1, math.ceil(total_count / size))
    items = list(first_page.get("items", []))

    print(f"Summaries page 1/{total_pages}: {len(items)}/{total_count}")
    for page in range(2, total_pages + 1):
        data = get_json(headers, SUMMARY_URL, {"country": args.country, "page": page}, limiter, args.insecure)
        page_items = data.get("items", [])
        items.extend(page_items)
        print(f"Summaries page {page}/{total_pages}: {len(items)}/{total_count}", flush=True)

    export = {
        "fetched_at": utc_now(),
        "page": 1,
        "size": size,
        "totalCount": total_count,
        "items": items,
    }
    write_json(output_path, export)
    print(f"Saved {len(items)} summaries to {output_path}")
    return export


def load_summaries(data_dir: Path) -> dict[str, Any]:
    path = data_dir / "receipts_summaries.json"
    if not path.exists():
        raise SystemExit(f"Missing {path}. Run the summaries command first.")
    return read_json(path)


def summary_item_date(item: dict[str, Any]) -> datetime | None:
    return parse_datetime(item.get("date") or item.get("purchaseDate") or item.get("createdAt"))


def max_summary_date(export: dict[str, Any]) -> datetime | None:
    dates = [summary_item_date(item) for item in export.get("items", [])]
    return max((d for d in dates if d is not None), default=None)


def min_summary_date(items: list[dict[str, Any]]) -> datetime | None:
    dates = [summary_item_date(item) for item in items]
    return min((d for d in dates if d is not None), default=None)


def merge_summary_items(existing: list[dict[str, Any]], new_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for item in existing:
        receipt_id = item.get("id")
        if receipt_id:
            merged[receipt_id] = item
    for item in new_items:
        receipt_id = item.get("id")
        if receipt_id:
            merged[receipt_id] = item

    return sorted(
        merged.values(),
        key=lambda item: summary_item_date(item) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )


def fetch_summaries_after(args: argparse.Namespace, since: datetime) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    cookie = require_cookie(args)
    headers = make_headers(cookie)
    limiter = RateLimiter(args.rate)
    output_path = args.data_dir / "receipts_summaries.json"

    existing_export = load_summaries(args.data_dir)
    existing_items = list(existing_export.get("items", []))
    first_page = get_json(headers, SUMMARY_URL, {"country": args.country, "page": 1}, limiter, args.insecure)
    size = int(first_page.get("size") or len(first_page.get("items", [])) or 10)
    total_count = int(first_page.get("totalCount") or len(first_page.get("items", [])))
    total_pages = max(1, math.ceil(total_count / size))

    fetched_items: list[dict[str, Any]] = []
    page = 1
    while True:
        data = first_page if page == 1 else get_json(
            headers,
            SUMMARY_URL,
            {"country": args.country, "page": page},
            limiter,
            args.insecure,
        )
        page_items = list(data.get("items", []))
        fetched_items.extend(page_items)
        page_min = min_summary_date(page_items)
        print(
            f"Summaries page {page}/{total_pages}: {len(page_items)} items, min date {format_datetime(page_min)}",
            flush=True,
        )
        if page >= total_pages or (page_min is not None and since >= page_min):
            break
        page += 1

    new_items = [item for item in fetched_items if (summary_item_date(item) or datetime.min.replace(tzinfo=timezone.utc)) > since]
    merged_items = merge_summary_items(existing_items, new_items)
    export = {
        "fetched_at": utc_now(),
        "page": 1,
        "size": size,
        "totalCount": max(total_count, len(merged_items)),
        "items": merged_items,
    }
    write_json(output_path, export)
    print(f"Saved {len(merged_items)} summaries to {output_path}; new since checkpoint: {len(new_items)}")
    return export, new_items


def fetch_detail(headers: dict[str, str], receipt_id: str, args: argparse.Namespace, limiter: RateLimiter) -> Any:
    return get_json(
        headers,
        DETAIL_URL.format(ticket_id=receipt_id),
        {"country": args.country, "languageCode": args.language_code},
        limiter,
        args.insecure,
    )


def fetch_details(args: argparse.Namespace, receipt_ids: list[str] | None = None) -> None:
    cookie = require_cookie(args)
    if receipt_ids is None:
        export = load_summaries(args.data_dir)
        receipt_ids = [item["id"] for item in export.get("items", []) if item.get("id")]
    raw_dir = args.data_dir / "receipts"
    raw_dir.mkdir(parents=True, exist_ok=True)
    existing = {p.stem for p in raw_dir.glob("*.json") if p.name != "_manifest.json"}
    to_fetch = [rid for rid in receipt_ids if rid not in existing]

    print(f"Total: {len(receipt_ids)}, already fetched: {len(existing)}, remaining: {len(to_fetch)}")
    if not to_fetch:
        return

    headers = make_headers(cookie)
    limiter = RateLimiter(args.rate)
    errors: list[dict[str, str]] = []
    success = 0
    start = time.time()

    for index, receipt_id in enumerate(to_fetch, start=1):
        try:
            data = fetch_detail(headers, receipt_id, args, limiter)
            write_json(raw_dir / f"{receipt_id}.json", data)
            success += 1
        except Exception as exc:  # noqa: BLE001 - report and continue
            errors.append({"id": receipt_id, "error": f"{type(exc).__name__}: {exc}"})

        if index % 25 == 0 or index == len(to_fetch):
            elapsed = max(time.time() - start, 0.001)
            print(
                f"Details {index}/{len(to_fetch)} | OK:{success} ERR:{len(errors)} | {elapsed:.0f}s",
                flush=True,
            )

    manifest = {
        "fetched_at": utc_now(),
        "total_receipts": len(receipt_ids),
        "already_present_at_start": len(existing),
        "successfully_fetched_this_run": success,
        "errors": errors,
    }
    write_json(raw_dir / "_manifest.json", manifest)
    if errors:
        print("First errors:")
        for error in errors[:5]:
            print(f"  {error['id']}: {error['error']}")


def command_status(args: argparse.Namespace) -> None:
    export = load_summaries(args.data_dir)
    newest = max_summary_date(export)
    now = datetime.now(timezone.utc)
    age_hours = None if newest is None else round((now - newest).total_seconds() / 3600, 2)
    should_fetch = newest is None or now > newest + timedelta(hours=args.refresh_after_hours)
    print(
        json.dumps(
            {
                "utc_now": format_datetime(now),
                "max_receipt_date": format_datetime(newest),
                "age_hours": age_hours,
                "refresh_after_hours": args.refresh_after_hours,
                "should_refresh": should_fetch,
            },
            indent=2,
        )
    )


def _get_attr(html_text: str, tag_pattern: str) -> str | None:
    """Extract an HTML attribute value from a matching tag pattern."""
    m = re.search(tag_pattern, html_text)
    if m:
        return m.group(1)
    return None


def _parse_lidl_date(text: str) -> str | None:
    """Parse a Lidl receipt date like '16/12/24' into ISO format '2024-12-16'."""
    if not text:
        return None
    m = re.search(r"Date:\s*(\d{2})/(\d{2})/(\d{2})", text)
    if m:
        day, month, year_short = m.group(1), m.group(2), m.group(3)
        year = "20" + year_short
        return f"{year}-{month}-{day}"
    return None


def parse_html_receipt(html_text: str) -> dict[str, Any]:
    """Parse a Lidl UK printed receipt HTML.

    The Lidl HTML uses a monospace <pre> layout with <span> elements carrying
    the actual data in HTML attributes (data-art-description, data-unit-price,
    data-promotion-id, etc.). This parser uses those attributes rather than
    trying to reverse-engineer the monospace display text.
    """
    result: dict[str, Any] = {
        "articles": [],
        "discounts": [],
        "vat_breakdown": [],
        "store_name": None,
        "store_city": None,
        "store_address": None,
        "store_postal": None,
        "date": None,
        "total_displayed": None,
        "payment_method": None,
        "card_last4": None,
        "articles_total": 0,
        "discounts_total": 0,
        "computed_net_total": 0,
        "loyalty_points": None,
    }

    # --- Store info ---
    store_m = re.search(
        r'<span[^>]*id="header_line_1"[^>]*>(.*?)</span>',
        html_text, re.DOTALL
    )
    if store_m:
        store_text = html.unescape(store_m.group(1)).strip()
        if store_text:
            result["store_name"] = store_text

    # --- Date from tender information ---
    date_span = re.search(r'<span[^>]*id="purchase_tender_information_3"[^>]*>(.*?)</span>', html_text)
    if date_span:
        result["date"] = _parse_lidl_date(date_span.group(1))

    # --- Total from purchase_summary_2 css_bold ---
    total_re = re.search(
        r'<span[^>]*id="purchase_summary_2"[^>]*class="[^"]*css_bold[^"]*"[^>]*>\s*([\d\.]+)\s*</span>',
        html_text,
    )
    if total_re:
        result["total_displayed"] = _parse_price(total_re.group(1))

    # --- Payment method ---
    payment_re = re.search(r'data-tender-description="([^"]*)"', html_text)
    if payment_re:
        result["payment_method"] = payment_re.group(1)

    # --- Card last 4 digits ---
    card_re = re.search(r'\*{3,}?(\d{4})', html_text)
    if card_re:
        result["card_last4"] = card_re.group(1)

    # --- VAT breakdown ---
    for vat_match in re.finditer(
        r'data-tax-type="([^"]*)"[^>]*data-tax-percentage="([^"]*)"[^>]*'
        r'data-tax-base-amount="([^"]*)"[^>]*data-tax-amount="([^"]*)"',
        html_text,
    ):
        result["vat_breakdown"].append({
            "type": vat_match.group(1),
            "percentage": _parse_price(vat_match.group(2)),
            "base_amount": _parse_price(vat_match.group(3)),
            "amount": _parse_price(vat_match.group(4)),
        })

    # --- Articles: use data-art-* attributes ---
    seen_article_ids: set[str] = set()
    for art_match in re.finditer(
        r'<span[^>]*class="article"[^>]*data-art-id="([^"]*)"[^>]*>'
        r'(.*?)</span>',
        html_text,
        re.DOTALL,
    ):
        art_id = art_match.group(1)
        span_html = art_match.group(0)

        description = _get_attr(span_html, r'data-art-description="([^"]*)"')
        unit_price = _get_attr(span_html, r'data-unit-price="([^"]*)"')
        quantity = _get_attr(span_html, r'data-art-quantity="([^"]*)"')
        tax_type = _get_attr(span_html, r'data-tax-type="([^"]*)"')

        # Skip weight-continuation rows (same art id as previous, no unit-price)
        if unit_price is None and art_id in seen_article_ids:
            continue

        seen_article_ids.add(art_id)

        description_clean = html.unescape(description or "").strip()
        # Strip trailing art id from description if present
        if description_clean and description_clean.split()[-1] == art_id:
            description_clean = " ".join(description_clean.split()[:-1])

        # Compute total price
        up = _parse_price(unit_price) if unit_price else None
        qty = _parse_price(quantity) if quantity else None
        price = None
        if qty is not None and up is not None:
            price = round(qty * up, 2)
        elif up is not None:
            price = up

        result["articles"].append({
            "name": description_clean,
            "quantity": qty or 1,
            "unit_price": up,
            "price": price,
            "tax_type": tax_type,
            "art_id": art_id,
        })

    # --- Discounts: group by purchase_list_line number ---
    # Each discount entry is a set of consecutive spans with the same line id.
    # E.g. line 3 has: empty span, name span ("£5 off £35 spend"), empty spans, amount span ("-1.23")
    discount_entries: list[dict[str, Any]] = []
    current_line_id: str | None = None
    current_discount: dict[str, Any] | None = None

    for disc_span_match in re.finditer(
        r'<span[^>]*class="[^"]*\bdiscount\b[^"]*"'
        r'[^>]*>(.*?)</span>',
        html_text,
        re.DOTALL,
    ):
        span_html = disc_span_match.group(0)
        inner = disc_span_match.group(1).strip()

        line_m = re.search(r'id="purchase_list_line_([^"]*)"', span_html)
        line_id = line_m.group(1) if line_m else None

        # Line id changed -> finalize previous discount
        if line_id != current_line_id:
            if current_discount and (current_discount["name"] or current_discount["amount"] is not None):
                discount_entries.append(current_discount)
            current_discount = {"name": "", "amount": None, "line_id": line_id}
            current_line_id = line_id

        if not inner:
            continue

        inner_text = html.unescape(inner).strip()
        if not inner_text:
            continue

        price_val = _parse_price(inner_text)
        if price_val is not None and current_discount:
            if current_discount["amount"] is None:
                current_discount["amount"] = price_val
        elif current_discount:
            if not current_discount["name"]:
                current_discount["name"] = inner_text

    # Don't forget the last discount
    if current_discount and (current_discount["name"] or current_discount["amount"] is not None):
        discount_entries.append(current_discount)

    result["discounts"] = [
        {"name": d["name"], "amount": d["amount"]}
        for d in discount_entries
    ]

    # --- Totals ---
    result["articles_total"] = round(
        sum(a["price"] for a in result["articles"] if a["price"] is not None), 2
    )
    result["discounts_total"] = round(
        sum(d["amount"] for d in result["discounts"] if d["amount"] is not None), 2
    )
    articles_sum = result["articles_total"]
    discs_sum = result["discounts_total"]
    result["computed_net_total"] = round(articles_sum + discs_sum, 2)

    return result


def _parse_price(text: str) -> float | None:
    """Parse a price string.

    Only parses values that look like prices: an optional minus sign followed
    by digits with at most one decimal point. Full sentences like
    '£5 off £35 spend' are rejected.
    """
    if not text:
        return None
    text = text.strip()
    # Must match a simple price pattern: optional -, digits, optional dot+digits
    # Examples: "1.23", "-0.55", ".99", "5", "£5.99", "£5"
    m = re.match(r'^[£€\$\u00a4]?\s*(\-?\d{1,8}(?:\.\d{1,4})?)\s*$', text)
    if m:
        try:
            return round(float(m.group(1)), 2)
        except (ValueError, TypeError):
            return None

    # Also handle price-suffix: "5.99 A" or "2 x £0.99 1.98" - extract last numeric part
    # Only use this for strings that are clearly price-oriented (short, mostly numeric)
    parts = re.split(r'\s{2,}', text)
    for part in reversed(parts):
        part = part.strip()
        m = re.match(r'^[£€\$\u00a4]?\s*(\-?\d{1,8}(?:\.\d{1,4})?)\s*$', part)
        if m:
            return round(float(m.group(1)), 2)

    return None


def parse_details(args: argparse.Namespace) -> dict[str, Any]:
    raw_dir = args.data_dir / "receipts"
    detail_paths = sorted(raw_dir.glob("*.json"))
    detail_paths = [p for p in detail_paths if p.name != "_manifest.json"]

    if not detail_paths:
        raise SystemExit(f"No receipt detail files found in {raw_dir}. Fetch details first.")

    all_receipts: list[dict[str, Any]] = []
    for detail_path in detail_paths:
        try:
            detail = read_json(detail_path)
        except Exception as exc:
            print_err(f"Skipping unreadable {detail_path}: {exc}")
            continue

        ticket = detail.get("ticket", {})
        html_receipt = ticket.get("htmlPrintedReceipt") or ticket.get("htmlReceipt") or detail.get("htmlPrintedReceipt") or detail.get("htmlReceipt")
        if not html_receipt:
            print_err(f"No HTML receipt in {detail_path.name}, keeping raw file available.")
            continue

        parsed = parse_html_receipt(html_receipt)
        parsed["id"] = detail_path.stem
        all_receipts.append(parsed)

    total_articles = sum(r.get("article_count", 0) or len(r.get("articles", [])) for r in all_receipts)
    total_discounts = sum(r.get("discount_count", 0) or len(r.get("discounts", [])) for r in all_receipts)
    total_spent = sum(r.get("computed_net_total", 0) or r.get("total_displayed", 0) or 0 for r in all_receipts)

    all_receipts.sort(
        key=lambda r: r.get("date") or "",
        reverse=True,
    )

    output = {
        "parsed_at": utc_now(),
        "total_receipts": len(all_receipts),
        "total_articles": total_articles,
        "total_discounts": total_discounts,
        "total_spent": round(total_spent, 2),
        "receipts": all_receipts,
    }
    write_json(args.data_dir / "receipts_detail.json", output)
    print(f"Parsed {len(all_receipts)} receipts, saved to {args.data_dir / 'receipts_detail.json'}")
    return output


def load_detail(args: argparse.Namespace) -> dict[str, Any]:
    path = args.data_dir / "receipts_detail.json"
    if not path.exists():
        raise SystemExit(f"Missing {path}. Parse details first.")
    return read_json(path)


def query_receipts(args: argparse.Namespace) -> None:
    detail = load_detail(args)

    start_dt = parse_datetime(args.start) if args.start else None
    end_dt = parse_datetime(args.end) if args.end else None
    now_utc = datetime.now(timezone.utc)

    if not start_dt and args.days is not None:
        start_dt = now_utc - timedelta(days=args.days)

    filtered = detail.get("receipts", [])
    if start_dt or end_dt:
        filtered = [
            r for r in filtered
            if _receipt_in_range(r, start_dt, end_dt)
        ]

    if not filtered:
        print(json.dumps({"receipts": [], "count": 0}, indent=2))
        return

    output = {
        "parsed_at": detail.get("parsed_at"),
        "query_start": format_datetime(start_dt),
        "query_end": format_datetime(end_dt),
        "total_receipts": len(filtered),
        "total_articles": sum(len(r.get("articles", [])) for r in filtered),
        "total_discounts": sum(len(r.get("discounts", [])) for r in filtered),
        "total_spent": round(sum(r.get("computed_net_total", 0) or r.get("total_displayed", 0) or 0 for r in filtered), 2),
        "receipts": filtered,
    }

    if args.include_articles:
        print(json.dumps(output, indent=2, ensure_ascii=False, default=str))
    else:
        condensed = {k: v for k, v in output.items() if k != "receipts"}
        condensed["receipts"] = [
            {
                "id": r.get("id"),
                "date": r.get("date"),
                "store_name": r.get("store_name"),
                "store_city": r.get("store_city"),
                "total_displayed": r.get("total_displayed"),
                "computed_net_total": r.get("computed_net_total"),
                "articles_total": r.get("articles_total"),
                "discounts_total": r.get("discounts_total"),
                "payment_method": r.get("payment_method"),
                "card_last4": r.get("card_last4"),
                "article_count": len(r.get("articles", [])),
                "discount_count": len(r.get("discounts", [])),
            }
            for r in filtered
        ]
        print(condensed["total_spent"])
        print(json.dumps(condensed, indent=2, ensure_ascii=False, default=str))


def _receipt_in_range(receipt: dict[str, Any], start: datetime | None, end: datetime | None) -> bool:
    rdate = parse_datetime(receipt.get("date"))
    if rdate is None:
        return False
    if start and rdate < start:
        return False
    if end and rdate >= end:
        return False
    return True


def all_receipts_summary(args: argparse.Namespace) -> None:
    summaries = load_summaries(args.data_dir)
    total_count = summaries.get("totalCount") or len(summaries.get("items", []))
    print(f"Total receipts in shipping address: {total_count}")


def command_update(args: argparse.Namespace) -> None:
    summaries = load_summaries(args.data_dir)
    checkpoint = max_summary_date(summaries)
    if checkpoint is None:
        print("No existing receipts found; running full export.")
        fetch_summaries(args)
        fetch_details(args)
        parse_details(args)
        return

    print(f"Checkpoint date: {format_datetime(checkpoint)}")
    merged_export, new_items = fetch_summaries_after(args, checkpoint)
    new_ids = [item["id"] for item in new_items if item.get("id")]
    if not new_ids:
        print("No new receipts since last update.")
        return

    print(f"Fetching details for {len(new_ids)} new receipt(s)...")
    fetch_details(args, new_ids)
    parse_details(args)
    detail = load_detail(args)
    new_receipts = [r for r in detail.get("receipts", []) if r.get("id") in new_ids]
    if args.include_articles and new_receipts:
        print("\n=== New Receipts ===")
        print(json.dumps(new_receipts, indent=2, ensure_ascii=False, default=str))
    else:
        print(f"Updated: {len(new_ids)} new receipt(s) fetched and parsed.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch and parse Lidl UK digital receipts.")

    parser.add_argument("--data-dir", type=Path, default=Path("data"), help="Output directory (default: data)")
    parser.add_argument("--country", default="GB", help="Country code (default: GB)")
    parser.add_argument("--language-code", default="en-GB", help="Language code (default: en-GB)")
    parser.add_argument("--cookie", help="Full Lidl Cookie header value")
    parser.add_argument("--cookie-stdin", action="store_true", help="Read Lidl Cookie header from stdin")
    parser.add_argument("--login", action="store_true", help="Derive cookie with Playwright browser auth")
    parser.add_argument("--email", help="Lidl login email")
    parser.add_argument("--password-stdin", action="store_true", help="Read Lidl password from stdin")
    parser.add_argument("--auth-state", type=Path, help="Custom Playwright storage-state path")
    parser.add_argument("--no-auth-state", action="store_true", help="Do not read or write browser auth state")
    parser.add_argument("--auth-headed", action="store_true", help="Show the auth browser")
    parser.add_argument("--auth-interactive", action="store_true", help="Open browser and wait for manual login")
    parser.add_argument("--auth-browser-channel", help="Playwright browser channel (e.g., chrome)")
    parser.add_argument("--auth-timeout", type=int, default=120, help="Auth login timeout in seconds (default: 120)")
    parser.add_argument("--rate", type=float, default=3.0, help="Max API requests per second (default: 3)")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS certificate verification")
    parser.add_argument("--refresh-after-hours", type=float, default=6.0, help="Age threshold for status command (default: 6)")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # all
    subparsers.add_parser("all", help="Fetch summaries, details, and parse all receipts")

    # status
    status_parser = subparsers.add_parser("status", help="Show last fetch status and whether refresh is needed")
    status_parser.add_argument("--refresh-after-hours", type=float, default=6.0)

    # summaries
    subparsers.add_parser("summaries", help="Fetch receipt summaries from Lidl API")

    # summaries-since
    summaries_since = subparsers.add_parser("summaries-since", help="Fetch summary pages newer than local checkpoint")
    summaries_since.add_argument("--since", help="ISO datetime checkpoint (default: from existing file)")

    # details
    details_parser = subparsers.add_parser("details", help="Fetch detail JSON for all summary receipts")
    details_parser.add_argument("--ids", nargs="*", help="Specific receipt IDs to fetch")

    # parse
    parse_parser = subparsers.add_parser("parse", help="Parse saved raw receipts into structured JSON")

    # query
    query_parser = subparsers.add_parser("query", help="Query parsed receipt data")
    query_parser.add_argument("--start", help="Inclusive start date (ISO format)")
    query_parser.add_argument("--end", help="Exclusive end date (ISO format)")
    query_parser.add_argument("--days", type=int, help="Receipts from last N days")
    query_parser.add_argument("--include-articles", action="store_true", help="Include full article and discount details")

    # update
    update_parser = subparsers.add_parser("update", help="Fetch only new receipts since last check")
    update_parser.add_argument("--include-articles", action="store_true", help="Print full article details for new receipts")

    args = parser.parse_args()

    if args.command == "all":
        fetch_summaries(args)
        fetch_details(args)
        parse_details(args)
    elif args.command == "status":
        command_status(args)
    elif args.command == "summaries":
        fetch_summaries(args)
    elif args.command == "summaries-since":
        if args.since:
            since = parse_datetime(args.since)
            if since is None:
                raise SystemExit(f"Invalid --since date: {args.since}")
            fetch_summaries_after(args, since)
        else:
            summaries = load_summaries(args.data_dir)
            checkpoint = max_summary_date(summaries)
            if checkpoint is None:
                raise SystemExit("No receipts found in existing data. Provide --since or run summaries first.")
            fetch_summaries_after(args, checkpoint)
    elif args.command == "details":
        fetch_details(args, args.ids)
    elif args.command == "parse":
        parse_details(args)
    elif args.command == "query":
        query_receipts(args)
    elif args.command == "update":
        command_update(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
