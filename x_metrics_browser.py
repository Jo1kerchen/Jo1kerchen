#!/usr/bin/env python3
"""Local X/Twitter metrics scraper via Playwright (no API token, no forced login)."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional

from playwright.sync_api import Browser
from playwright.sync_api import BrowserContext
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


@dataclass
class CsvRow:
    input_url: str
    tweet_id: str = ""
    is_repost: str = ""
    current_likes: str = ""
    current_replies: str = ""
    current_reposts: str = ""
    current_views: str = ""
    original_tweet_id: str = ""
    original_likes: str = ""
    original_replies: str = ""
    original_reposts: str = ""
    original_views: str = ""
    status: str = "failed"
    error: str = ""


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


def extract_views(page: Page, debug_views: bool = False) -> str:
    keywords = ["view", "views", "浏览", "瀏覽", "查看", "次查看", "impression", "impressions"]

    value = extract_metric(
        page,
        testids=["analytics"],
        keywords=keywords,
        text_patterns=["View", "Views", "浏览", "瀏覽", "查看", "次查看", "Impression", "Impressions"],
    )
    if value != "N/A":
        debug(f"views from analytics/test patterns: {value}", debug_views)
        return value

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

    return "N/A"


def collect_metrics(page: Page, debug_views: bool = False) -> Metrics:
    likes = extract_metric(
        page,
        testids=["like", "unlike"],
        keywords=["like", "喜欢", "喜歡", "赞", "讚"],
        text_patterns=["Like", "Likes", "喜欢", "喜歡", "赞", "讚"],
    )
    replies = extract_metric(
        page,
        testids=["reply"],
        keywords=["repl", "回复", "回覆", "评论", "評論"],
        text_patterns=["Reply", "Replies", "回复", "回覆", "评论", "評論"],
    )
    reposts = extract_metric(
        page,
        testids=["retweet", "unretweet"],
        keywords=["repost", "retweet", "转发", "轉發"],
        text_patterns=["Repost", "Reposts", "Retweet", "Retweets", "转发", "轉發"],
    )

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


def scrape_single(page: Page, input_url: str, timeout_ms: int, debug_views: bool) -> tuple[CsvRow, Optional[Metrics], Optional[Metrics]]:
    row = CsvRow(input_url=input_url)
    current_metrics: Optional[Metrics] = None
    original_metrics: Optional[Metrics] = None

    normalized_url = normalize_url(input_url)
    current_tweet_id = extract_tweet_id(normalized_url)
    row.tweet_id = current_tweet_id

    page.goto(normalized_url, wait_until="domcontentloaded", timeout=timeout_ms)
    dismiss_blocking_overlays(page)
    wait_for_main_post(page, timeout_ms)

    current_metrics = collect_metrics(page, debug_views=debug_views)
    is_repost, original_id = detect_repost_and_original(page, current_tweet_id)

    row.is_repost = "Yes" if is_repost else "No"
    row.current_likes = current_metrics.likes
    row.current_replies = current_metrics.replies
    row.current_reposts = current_metrics.reposts
    row.current_views = current_metrics.views

    if not has_any_metric(current_metrics):
        warn("当前页面在未登录状态下无法稳定提取数据")

    if is_repost and original_id:
        row.original_tweet_id = original_id
        original_url = f"https://x.com/i/status/{original_id}"
        try:
            page.goto(original_url, wait_until="domcontentloaded", timeout=timeout_ms)
            dismiss_blocking_overlays(page)
            wait_for_main_post(page, timeout_ms)
            original_metrics = collect_metrics(page, debug_views=debug_views)
            row.original_likes = original_metrics.likes
            row.original_replies = original_metrics.replies
            row.original_reposts = original_metrics.reposts
            row.original_views = original_metrics.views
        except Exception as exc:  # noqa: BLE001
            warn(f"Failed to extract original post metrics: {exc}")
            row.error = f"original extraction failed: {exc}"
    elif is_repost:
        row.error = "unable to identify original post ID"

    row.status = "success"
    return row, current_metrics, original_metrics


def parse_multiline_urls(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def read_links_from_file(path: str) -> list[str]:
    p = Path(path)
    if not p.exists():
        raise XBrowserMetricsError(f"Input file not found: {path}")

    lines = p.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip()]


def gather_input_urls(args: argparse.Namespace) -> list[str]:
    urls: list[str] = []

    if args.input_file:
        urls.extend(read_links_from_file(args.input_file))
    if args.urls:
        urls.extend([u.strip() for u in args.urls if u.strip()])

    if not urls:
        raise XBrowserMetricsError("No input URLs provided. Use positional URL(s) or --input-file.")
    return urls


def write_csv(rows: list[CsvRow], csv_path: str) -> None:
    fieldnames = list(CsvRow.__annotations__.keys())
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def rows_to_csv_text(rows: list[CsvRow]) -> str:
    fieldnames = list(CsvRow.__annotations__.keys())
    from io import StringIO

    buf = StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow(asdict(row))
    return buf.getvalue()


def process_urls(
    urls: list[str],
    *,
    headless: bool = False,
    timeout_s: int = 30,
    debug_views: bool = False,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> list[CsvRow]:
    if not urls:
        return []

    timeout_ms = max(timeout_s, 5) * 1000
    rows: list[CsvRow] = []

    with sync_playwright() as p:
        browser: Browser = p.chromium.launch(headless=headless)
        context: BrowserContext = browser.new_context(viewport={"width": 1400, "height": 900})
        page: Page = context.new_page()

        try:
            total = len(urls)
            for index, input_url in enumerate(urls, start=1):
                if progress_callback:
                    progress_callback(index, total, input_url)

                try:
                    row, _, _ = scrape_single(
                        page=page,
                        input_url=input_url,
                        timeout_ms=timeout_ms,
                        debug_views=debug_views,
                    )
                    rows.append(row)
                except Exception as exc:  # noqa: BLE001
                    fail_row = CsvRow(input_url=input_url, status="failed", error=str(exc))
                    try:
                        fail_row.tweet_id = extract_tweet_id(normalize_url(input_url))
                    except Exception:  # noqa: BLE001
                        pass
                    rows.append(fail_row)
        finally:
            context.close()
            browser.close()

    return rows


def print_single_output(row: CsvRow) -> None:
    current_metrics = Metrics(
        likes=row.current_likes or "N/A",
        replies=row.current_replies or "N/A",
        reposts=row.current_reposts or "N/A",
        views=row.current_views or "N/A",
    )

    print(f"\nInput URL: {row.input_url}")
    print(f"Tweet ID: {row.tweet_id}")
    print(f"Is repost: {row.is_repost}")
    print_metrics_block("Current post metrics", row.tweet_id, current_metrics)

    if row.is_repost == "Yes":
        if row.original_tweet_id:
            if any([row.original_likes, row.original_replies, row.original_reposts, row.original_views]):
                original_metrics = Metrics(
                    likes=row.original_likes or "N/A",
                    replies=row.original_replies or "N/A",
                    reposts=row.original_reposts or "N/A",
                    views=row.original_views or "N/A",
                )
                print_metrics_block("Original post metrics", row.original_tweet_id, original_metrics)
            else:
                print("\nOriginal post metrics: N/A (unable to access or extract original post)")
        else:
            print("\nOriginal post metrics: N/A (unable to identify original post ID)")


def run(args: argparse.Namespace) -> int:
    urls = gather_input_urls(args)
    is_batch = len(urls) > 1 or bool(args.input_file) or bool(args.output_csv)

    log("Launching browser...")

    def cli_progress(i: int, total: int, url: str) -> None:
        print(f"[{i}/{total}] Processing: {url}")

    try:
        rows = process_urls(
            urls,
            headless=args.headless,
            timeout_s=args.timeout,
            debug_views=args.debug_views,
            progress_callback=cli_progress,
        )

        if not is_batch and rows:
            print_single_output(rows[0])

        if args.output_csv:
            write_csv(rows, args.output_csv)
            log(f"CSV written: {args.output_csv}")

        if is_batch and not args.output_csv:
            log("Batch run finished. Use --output-csv to save structured results.")

        log("Extraction succeeded")
        return 0
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
    parser.add_argument("urls", nargs="*", help="One or multiple X/Twitter status URLs")
    parser.add_argument("--input-file", help="Read URLs from text file (one URL per line)")
    parser.add_argument("--output-csv", help="Write batch results to CSV file")
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
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
