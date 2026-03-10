#!/usr/bin/env python3
"""Local X/Twitter metrics scraper via Playwright (no API token, no forced login)."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from typing import Optional

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

STATUS_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:x|twitter)\.com/[^/]+/(?:status|statuses)/(\d+)",
    re.IGNORECASE,
)
NUMBER_RE = re.compile(r"(\d[\d,.]*\s*[KMBkmb]?)")
WAN_VIEW_RE = re.compile(r"(\d+(?:\.\d+)?)\s*万\s*(?:次)?(?:查看|浏览|瀏覽)")


class XBrowserMetricsError(Exception):
    """Custom CLI exception for user-friendly errors."""


@dataclass
class Metrics:
    likes: str = "N/A"
    replies: str = "N/A"
    reposts: str = "N/A"
    views: str = "N/A"


def log(msg: str) -> None:
    print(f"[INFO] {msg}")


def warn(msg: str) -> None:
    print(f"[WARN] {msg}")


def debug(msg: str, enabled: bool) -> None:
    if enabled:
        print(f"[DEBUG_VIEWS] {msg}")


def normalize_url(url: str) -> str:
    url = url.strip()
    return re.sub(r"https?://(?:www\.)?twitter\.com", "https://x.com", url, flags=re.IGNORECASE)


def extract_tweet_id(url: str) -> str:
    m = STATUS_URL_RE.search(url.strip())
    if not m:
        raise XBrowserMetricsError(
            "Invalid URL. Please use a status link like https://x.com/<user>/status/<tweet_id>."
        )
    return m.group(1)


def normalize_count(raw: str) -> Optional[str]:
    if not raw:
        return None
    cleaned = raw.strip().replace(" ", "").replace(",", "")
    m = re.match(r"^(\d+(?:\.\d+)?)([KMBkmb]?)$", cleaned)
    if not m:
        return None
    value = float(m.group(1))
    suffix = m.group(2).upper()
    multiplier = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[suffix]
    return str(int(value * multiplier))


def first_count(text: str) -> Optional[str]:
    if not text:
        return None

    # 中文“2万次查看”
    wan_match = WAN_VIEW_RE.search(text)
    if wan_match:
        return str(int(float(wan_match.group(1)) * 10_000))

    m = NUMBER_RE.search(text.replace("\n", " "))
    if not m:
        return None
    return normalize_count(m.group(1))


def wait_for_main_post(page: Page, timeout_ms: int) -> None:
    page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    page.wait_for_timeout(1200)

    selectors = [
        "article[data-testid='tweet']",
        "main article",
        "div[data-testid='primaryColumn'] article",
    ]

    for _ in range(6):
        if any(page.locator(sel).count() > 0 for sel in selectors):
            return
        page.wait_for_timeout(800)

    raise XBrowserMetricsError(
        "Could not find tweet article on the page. The tweet may be unavailable, blocked, or not fully loaded."
    )


def dismiss_blocking_overlays(page: Page) -> None:
    close_selectors = [
        "button[aria-label='Close']",
        "button[aria-label='关闭']",
        "button:has-text('Close')",
        "button:has-text('关闭')",
        "div[role='button']:has-text('Not now')",
        "div[role='button']:has-text('稍后')",
        "div[role='button']:has-text('以后再说')",
    ]
    for sel in close_selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                loc.click(timeout=800)
                page.wait_for_timeout(200)
        except PlaywrightError:
            continue

    try:
        page.keyboard.press("Escape")
    except PlaywrightError:
        pass


def extract_from_testids(page: Page, testids: list[str], keywords: list[str]) -> Optional[str]:
    for tid in testids:
        loc = page.locator(f"[data-testid='{tid}']")
        if loc.count() == 0:
            continue
        try:
            aria = loc.first.get_attribute("aria-label") or ""
            if aria:
                low = aria.lower()
                if any(k in low for k in keywords):
                    value = first_count(aria)
                    if value:
                        return value

            txt = loc.first.inner_text().strip()
            value = first_count(txt)
            if value:
                return value
        except PlaywrightError:
            continue
    return None


def extract_from_text_patterns(page: Page, patterns: list[str]) -> Optional[str]:
    for p in patterns:
        try:
            loc = page.locator(f"span:has-text('{p}')")
            if loc.count() == 0:
                continue

            txt = loc.first.inner_text().strip()
            value = first_count(txt)
            if value:
                return value

            parent_txt = loc.first.evaluate("el => (el.parentElement && el.parentElement.innerText) || el.innerText")
            value = first_count(parent_txt or "")
            if value:
                return value
        except PlaywrightError:
            continue
    return None


def extract_metric(page: Page, *, testids: list[str], keywords: list[str], text_patterns: list[str]) -> str:
    value = extract_from_testids(page, testids, keywords)
    if value:
        return value
    value = extract_from_text_patterns(page, text_patterns)
    if value:
        return value
    return "N/A"


def debug_view_candidates(page: Page) -> None:
    keywords = ["view", "views", "浏览", "查看", "次查看", "impression", "impressions"]

    print("\n[DEBUG_VIEWS] === 1) Nodes containing view keywords (text/aria/title/testid) ===")
    js = """
    (keywords) => {
      const out = [];
      const nodes = Array.from(document.querySelectorAll('article [aria-label], article [title], article [data-testid], article span, article div, article a, article button'));
      for (const node of nodes) {
        const aria = (node.getAttribute('aria-label') || '').trim();
        const title = (node.getAttribute('title') || '').trim();
        const testid = (node.getAttribute('data-testid') || '').trim();
        const txt = (node.innerText || node.textContent || '').trim().replace(/\s+/g, ' ');
        const blob = `${aria} ${title} ${testid} ${txt}`.toLowerCase();
        if (!blob) continue;
        if (keywords.some(k => blob.includes(k.toLowerCase()))) {
          out.push({tag: node.tagName, testid, aria, title, text: txt});
        }
      }
      return out.slice(0, 120);
    }
    """
    try:
        nodes = page.evaluate(js, keywords)
        for i, n in enumerate(nodes, start=1):
            print(
                f"[DEBUG_VIEWS][{i}] tag={n.get('tag')} testid={n.get('testid')!r} "
                f"aria={n.get('aria')!r} title={n.get('title')!r} text={n.get('text')!r}"
            )
    except PlaywrightError as exc:
        print(f"[DEBUG_VIEWS] Failed keyword scan: {exc}")

    print("\n[DEBUG_VIEWS] === 2) Metric-like nodes around tweet article ===")
    metric_selectors = [
        "article[data-testid='tweet'] [data-testid='reply']",
        "article[data-testid='tweet'] [data-testid='retweet'], article[data-testid='tweet'] [data-testid='unretweet']",
        "article[data-testid='tweet'] [data-testid='like'], article[data-testid='tweet'] [data-testid='unlike']",
        "article[data-testid='tweet'] [data-testid='analytics']",
        "article[data-testid='tweet'] div[role='group'] > *",
        "article[data-testid='tweet'] a[href*='/analytics']",
    ]
    idx = 1
    for sel in metric_selectors:
        try:
            loc = page.locator(sel)
            count = min(loc.count(), 20)
            if count == 0:
                continue
            print(f"[DEBUG_VIEWS] selector={sel!r}, count={count}")
            for i in range(count):
                node = loc.nth(i)
                txt = (node.inner_text() or "").strip().replace("\n", " ")
                aria = node.get_attribute("aria-label") or ""
                title = node.get_attribute("title") or ""
                testid = node.get_attribute("data-testid") or ""
                print(
                    f"[DEBUG_VIEWS][{idx}] testid={testid!r} aria={aria!r} title={title!r} text={txt!r}"
                )
                idx += 1
        except PlaywrightError:
            continue

    print("\n[DEBUG_VIEWS] === 3) Metric row raw children order (helps find where views sits) ===")
    try:
        rows = page.locator("article[data-testid='tweet'] div[role='group']")
        if rows.count() > 0:
            row = rows.first
            children = row.locator(":scope > *")
            child_count = min(children.count(), 30)
            for i in range(child_count):
                c = children.nth(i)
                txt = (c.inner_text() or "").strip().replace("\n", " ")
                aria = c.get_attribute("aria-label") or ""
                testid = c.get_attribute("data-testid") or ""
                print(f"[DEBUG_VIEWS][row:{i}] testid={testid!r} aria={aria!r} text={txt!r}")
    except PlaywrightError as exc:
        print(f"[DEBUG_VIEWS] metric row dump failed: {exc}")


def extract_views(page: Page, debug_views: bool = False) -> str:
    keywords = ["view", "views", "浏览", "瀏覽", "查看", "次查看", "impression", "impressions"]

    # 1) direct testid analytics
    value = extract_metric(
        page,
        testids=["analytics"],
        keywords=keywords,
        text_patterns=["View", "Views", "浏览", "瀏覽", "查看", "次查看", "Impression", "Impressions"],
    )
    if value != "N/A":
        debug(f"views from analytics/test patterns: {value}", debug_views)
        return value

    # 2) find any article node with views keywords in text/aria/title
    selectors = [
        "article[data-testid='tweet'] [aria-label]",
        "article[data-testid='tweet'] [title]",
        "article[data-testid='tweet'] a",
        "article[data-testid='tweet'] span",
        "article[data-testid='tweet'] div",
        "article[data-testid='tweet'] button",
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel)
            max_n = min(loc.count(), 120)
            for i in range(max_n):
                node = loc.nth(i)
                aria = node.get_attribute("aria-label") or ""
                title = node.get_attribute("title") or ""
                txt = (node.inner_text() or "").strip().replace("\n", " ")
                blob = f"{aria} {title} {txt}".lower()
                if any(k in blob for k in keywords):
                    candidate = first_count(f"{aria} {title} {txt}")
                    if candidate:
                        debug(f"views from keyword node ({sel}#{i}): {candidate} / {txt!r}", debug_views)
                        return candidate
        except PlaywrightError:
            continue

    # 3) try metric row's last meaningful element (often views)
    try:
        row = page.locator("article[data-testid='tweet'] div[role='group']").first
        if row.count() > 0:
            children = row.locator(":scope > *")
            child_count = children.count()
            for i in range(child_count - 1, -1, -1):
                txt = (children.nth(i).inner_text() or "").strip().replace("\n", " ")
                if not txt:
                    continue
                candidate = first_count(txt)
                if candidate:
                    debug(f"views from metric row fallback index={i}: {candidate} / {txt!r}", debug_views)
                    return candidate
    except PlaywrightError:
        pass

    return "N/A"


def collect_metrics(page: Page, debug_views: bool = False) -> Metrics:
    log("Extracting Likes...")
    likes = extract_metric(
        page,
        testids=["like", "unlike"],
        keywords=["like", "喜欢", "喜歡", "赞", "讚"],
        text_patterns=["Like", "Likes", "喜欢", "喜歡", "赞", "讚"],
    )

    log("Extracting Replies...")
    replies = extract_metric(
        page,
        testids=["reply"],
        keywords=["repl", "回复", "回覆", "评论", "評論"],
        text_patterns=["Reply", "Replies", "回复", "回覆", "评论", "評論"],
    )

    log("Extracting Reposts...")
    reposts = extract_metric(
        page,
        testids=["retweet", "unretweet"],
        keywords=["repost", "retweet", "转发", "轉發"],
        text_patterns=["Repost", "Reposts", "Retweet", "Retweets", "转发", "轉發"],
    )

    log("Extracting Views...")
    if debug_views:
        debug_view_candidates(page)
    views = extract_views(page, debug_views=debug_views)

    return Metrics(likes=likes, replies=replies, reposts=reposts, views=views)


def detect_repost_and_original(page: Page, current_tweet_id: str) -> tuple[bool, Optional[str]]:
    article = page.locator("article[data-testid='tweet']").first
    if article.count() == 0:
        return False, None

    is_repost = False
    try:
        blob = article.inner_text().lower()
        if any(key in blob for key in ["reposted", "retweet", "转推", "轉推", "转发了", "轉發了"]):
            is_repost = True
    except PlaywrightError:
        pass

    try:
        hrefs = article.evaluate_all(
            "els => els.flatMap(el => Array.from(el.querySelectorAll('a[href*=\"/status/\"]')).map(a => a.href))"
        )
    except PlaywrightError:
        hrefs = []

    for href in hrefs:
        m = STATUS_URL_RE.search(str(href))
        if m:
            other_id = m.group(1)
            if other_id != current_tweet_id:
                return True, other_id

    return is_repost, None


def has_any_metric(metrics: Metrics) -> bool:
    return any(v != "N/A" for v in [metrics.likes, metrics.replies, metrics.reposts, metrics.views])


def print_metrics_block(title: str, tweet_id: str, metrics: Metrics) -> None:
    print(f"\n{title}:")
    print(f"  Tweet ID: {tweet_id}")
    print(f"  Likes: {metrics.likes}")
    print(f"  Replies: {metrics.replies}")
    print(f"  Reposts: {metrics.reposts}")
    print(f"  Views: {metrics.views}")


def run(url: str, headless: bool, timeout_s: int, debug_views: bool) -> int:
    normalized_url = normalize_url(url)
    current_tweet_id = extract_tweet_id(normalized_url)
    timeout_ms = max(timeout_s, 5) * 1000

    log("Launching browser...")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(viewport={"width": 1400, "height": 900})
            page = context.new_page()

            try:
                log(f"Opening page... {normalized_url}")
                page.goto(normalized_url, wait_until="domcontentloaded", timeout=timeout_ms)
                dismiss_blocking_overlays(page)

                log("Attempting extraction without login...")
                wait_for_main_post(page, timeout_ms)
                log("Page loaded")

                log("Extracting current post metrics...")
                current_metrics = collect_metrics(page, debug_views=debug_views)
                is_repost, original_id = detect_repost_and_original(page, current_tweet_id)

                if not has_any_metric(current_metrics):
                    warn("当前页面在未登录状态下无法稳定提取数据")

                print(f"\nInput URL: {url}")
                print(f"Tweet ID: {current_tweet_id}")
                print(f"Is repost: {'Yes' if is_repost else 'No'}")
                print_metrics_block("Current post metrics", current_tweet_id, current_metrics)

                if is_repost and original_id:
                    log("Extracting original post metrics...")
                    original_url = f"https://x.com/i/status/{original_id}"
                    try:
                        page.goto(original_url, wait_until="domcontentloaded", timeout=timeout_ms)
                        dismiss_blocking_overlays(page)
                        wait_for_main_post(page, timeout_ms)
                        original_metrics = collect_metrics(page, debug_views=debug_views)
                        print_metrics_block("Original post metrics", original_id, original_metrics)
                    except Exception as exc:  # noqa: BLE001
                        warn(f"Failed to extract original post metrics: {exc}")
                        print("\nOriginal post metrics: N/A (unable to access or extract original post)")
                elif is_repost:
                    print("\nOriginal post metrics: N/A (unable to identify original post ID)")

                log("Extraction succeeded")
                return 0
            finally:
                context.close()
                browser.close()

    except XBrowserMetricsError as exc:
        print(f"Error: {exc}")
        return 1
    except (PlaywrightTimeoutError, PlaywrightError) as exc:
        print(f"Error: Playwright failure - {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"Error: Unexpected failure - {exc}")
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch X/Twitter metrics from webpage via Playwright.")
    parser.add_argument("url", help="X/Twitter status URL")
    parser.add_argument("--headless", action="store_true", help="Run in headless mode (default: non-headless)")
    parser.add_argument("--timeout", type=int, default=30, help="Page timeout in seconds (default: 30)")
    parser.add_argument(
        "--debug-views",
        action="store_true",
        help="Print detailed candidate nodes and raw texts used to debug views extraction",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return run(args.url, headless=args.headless, timeout_s=args.timeout, debug_views=args.debug_views)


if __name__ == "__main__":
    sys.exit(main())
