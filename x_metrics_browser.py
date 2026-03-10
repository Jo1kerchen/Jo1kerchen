#!/usr/bin/env python3
"""Fetch X/Twitter post metrics by reading the web page with Playwright."""

from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

STATUS_URL_RE = re.compile(r"https?://(?:www\.)?(?:x|twitter)\.com/[^/]+/(?:status|statuses)/(\d+)")
COUNT_RE = re.compile(r"([0-9][0-9,\.]*\s*[KMB]?)", re.IGNORECASE)


class XBrowserMetricsError(Exception):
    """Custom CLI error."""


@dataclass
class Metrics:
    likes: str = "N/A"
    replies: str = "N/A"
    reposts: str = "N/A"
    quotes: str = "N/A"
    views: str = "N/A"


def extract_tweet_id(url: str) -> str:
    match = STATUS_URL_RE.search(url.strip())
    if not match:
        raise XBrowserMetricsError(
            "Invalid X/Twitter status URL. Example: https://x.com/<user>/status/<tweet_id>"
        )
    return match.group(1)


def normalize_url(url: str) -> str:
    return re.sub(r"https?://(?:www\.)?twitter\.com", "https://x.com", url.strip())


def maybe_wait_for_manual_login(page, timeout_s: int) -> None:
    page.wait_for_timeout(1500)
    login_indicators = [
        "text=Sign in",
        "text=Log in",
        "input[name='text']",
        "input[autocomplete='username']",
    ]
    needs_login = any(page.locator(selector).first.count() > 0 for selector in login_indicators)

    if needs_login:
        print("\n[提示] 检测到可能需要登录 X。请在打开的浏览器窗口中完成登录。")
        print("[提示] 登录完成并能看到帖子详情页后，在终端按回车继续...\n")
        input()
        try:
            page.wait_for_load_state("domcontentloaded", timeout=timeout_s * 1000)
        except PlaywrightTimeoutError:
            pass


def wait_for_post_ready(page, timeout_s: int) -> None:
    page.wait_for_load_state("domcontentloaded", timeout=timeout_s * 1000)
    page.wait_for_timeout(1200)

    if page.locator("article[data-testid='tweet']").count() == 0:
        # retry once for slower rendering
        page.wait_for_timeout(2000)

    if page.locator("article[data-testid='tweet']").count() == 0:
        raise XBrowserMetricsError("未找到帖子内容（article[data-testid='tweet']）。请确认链接有效且页面可访问。")


def first_group(text: str) -> Optional[str]:
    if not text:
        return None
    m = COUNT_RE.search(text.replace("\n", " "))
    return m.group(1).strip() if m else None


def count_from_aria(page, testid_candidates: list[str], keyword: str) -> str:
    for testid in testid_candidates:
        button = page.locator(f"[data-testid='{testid}']").first
        if button.count() == 0:
            continue

        aria_label = button.get_attribute("aria-label") or ""
        if aria_label and keyword.lower() in aria_label.lower():
            value = first_group(aria_label)
            if value:
                return value

        txt = button.inner_text().strip()
        value = first_group(txt)
        if value:
            return value
    return "N/A"


def quote_count_from_page(page) -> str:
    quote_link = page.locator("a[href*='/retweets/with_comments']").first
    if quote_link.count() > 0:
        text = quote_link.inner_text().strip()
        value = first_group(text)
        if value:
            return value

    candidates = page.locator("span:has-text('Quote'), span:has-text('Quotes')")
    if candidates.count() > 0:
        text = candidates.first.evaluate("el => el.parentElement ? el.parentElement.innerText : el.innerText")
        value = first_group(text or "")
        if value:
            return value

    return "N/A"


def collect_metrics(page) -> Metrics:
    return Metrics(
        likes=count_from_aria(page, ["like", "unlike"], "like"),
        replies=count_from_aria(page, ["reply"], "repl"),
        reposts=count_from_aria(page, ["retweet", "unretweet"], "repost"),
        quotes=quote_count_from_page(page),
        views=count_from_aria(page, ["analytics"], "view"),
    )


def detect_repost_and_original(page, current_tweet_id: str) -> tuple[bool, Optional[str]]:
    article = page.locator("article[data-testid='tweet']").first
    if article.count() == 0:
        return False, None

    is_repost = False
    text_blob = article.inner_text().lower()
    if "reposted" in text_blob or "retweet" in text_blob:
        is_repost = True

    hrefs = article.evaluate_all(
        "els => els.flatMap(el => Array.from(el.querySelectorAll('a[href*="/status/"]')).map(a => a.href))"
    )
    ids = []
    for href in hrefs:
        m = STATUS_URL_RE.search(href)
        if m:
            ids.append(m.group(1))

    for tid in ids:
        if tid != current_tweet_id:
            return True, tid

    return is_repost, None


def print_block(title: str, tweet_id: str, m: Metrics) -> None:
    print(f"{title}:")
    print(f"  Tweet ID: {tweet_id}")
    print(f"  Likes: {m.likes}")
    print(f"  Replies: {m.replies}")
    print(f"  Reposts: {m.reposts}")
    print(f"  Quotes: {m.quotes}")
    print(f"  Views: {m.views}")


def run(url: str, user_data_dir: Path, headless: bool, timeout_s: int) -> int:
    input_url = normalize_url(url)
    current_tweet_id = extract_tweet_id(input_url)
    user_data_dir.mkdir(parents=True, exist_ok=True)

    try:
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(user_data_dir),
                headless=headless,
                viewport={"width": 1440, "height": 900},
            )
            page = context.pages[0] if context.pages else context.new_page()

            try:
                page.goto(input_url, wait_until="domcontentloaded", timeout=timeout_s * 1000)
                maybe_wait_for_manual_login(page, timeout_s)
                wait_for_post_ready(page, timeout_s)

                current_metrics = collect_metrics(page)
                is_repost, original_tweet_id = detect_repost_and_original(page, current_tweet_id)

                print(f"Input URL: {url}")
                print(f"Tweet ID: {current_tweet_id}")
                print(f"Is repost: {'Yes' if is_repost else 'No'}")
                print_block("Current post metrics", current_tweet_id, current_metrics)

                if is_repost and original_tweet_id:
                    original_url = f"https://x.com/i/status/{original_tweet_id}"
                    page.goto(original_url, wait_until="domcontentloaded", timeout=timeout_s * 1000)
                    wait_for_post_ready(page, timeout_s)
                    original_metrics = collect_metrics(page)
                    print_block("Original post metrics", original_tweet_id, original_metrics)
                elif is_repost:
                    print("Original post metrics: N/A (未成功识别原帖 ID)")

            finally:
                # Give disk state a tiny moment to flush if user just logged in.
                time.sleep(0.5)
                context.close()

    except XBrowserMetricsError as exc:
        print(f"Error: {exc}")
        return 1
    except (PlaywrightTimeoutError, PlaywrightError) as exc:
        print(f"Error: Playwright failure - {exc}")
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"Error: Unexpected failure - {exc}")
        return 1

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch X/Twitter metrics directly from webpage via Playwright.")
    parser.add_argument("url", help="X/Twitter status URL")
    parser.add_argument(
        "--user-data-dir",
        default=".x_browser_profile",
        help="Persistent browser profile directory (default: .x_browser_profile)",
    )
    parser.add_argument("--headless", action="store_true", help="Run headless (not recommended for first login)")
    parser.add_argument("--timeout", type=int, default=30, help="Page timeout in seconds (default: 30)")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return run(args.url, Path(args.user_data_dir), args.headless, args.timeout)


if __name__ == "__main__":
    sys.exit(main())
