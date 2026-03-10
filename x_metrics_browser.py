#!/usr/bin/env python3
"""Local X/Twitter metrics scraper with Playwright (no API token needed)."""

from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from playwright.sync_api import BrowserContext
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

STATUS_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:x|twitter)\.com/[^/]+/(?:status|statuses)/(\d+)",
    re.IGNORECASE,
)
RAW_NUMBER_RE = re.compile(r"(\d[\d,.]*\s*[KMBkmb]?)")


class XBrowserMetricsError(Exception):
    """Custom CLI error."""


@dataclass
class Metrics:
    likes: str = "N/A"
    replies: str = "N/A"
    reposts: str = "N/A"
    quotes: str = "N/A"
    views: str = "N/A"


@dataclass
class ScrapeContext:
    page: Page
    timeout_ms: int


def log(message: str) -> None:
    print(f"[INFO] {message}")


def warn(message: str) -> None:
    print(f"[WARN] {message}")


def normalize_url(url: str) -> str:
    url = url.strip()
    return re.sub(r"https?://(?:www\.)?twitter\.com", "https://x.com", url, flags=re.IGNORECASE)


def extract_tweet_id(url: str) -> str:
    match = STATUS_URL_RE.search(url)
    if not match:
        raise XBrowserMetricsError(
            "无效链接：请提供 x.com/twitter.com 的状态页链接，例如 https://x.com/<user>/status/<id>"
        )
    return match.group(1)


def normalize_count(raw: str) -> Optional[str]:
    if not raw:
        return None
    cleaned = raw.strip().replace(" ", "").replace(",", "")
    m = re.match(r"^(\d+(?:\.\d+)?)([KMBkmb]?)$", cleaned)
    if not m:
        return None

    base = float(m.group(1))
    suffix = m.group(2).upper()
    multiplier = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[suffix]
    value = int(base * multiplier)
    return str(value)


def first_count_from_text(text: str) -> Optional[str]:
    if not text:
        return None
    m = RAW_NUMBER_RE.search(text.replace("\n", " "))
    if not m:
        return None
    return normalize_count(m.group(1))


def ensure_post_loaded(ctx: ScrapeContext) -> None:
    page = ctx.page
    page.wait_for_load_state("domcontentloaded", timeout=ctx.timeout_ms)
    page.wait_for_timeout(1200)

    selectors = [
        "article[data-testid='tweet']",
        "div[data-testid='primaryColumn'] article",
        "main article",
    ]

    for _ in range(4):
        if any(page.locator(sel).count() > 0 for sel in selectors):
            return
        page.wait_for_timeout(1000)

    raise XBrowserMetricsError("页面未加载出推文内容（article）。请确认链接有效、网络可用或已登录。")


def needs_login(page: Page) -> bool:
    login_selectors = [
        "input[autocomplete='username']",
        "input[name='text']",
        "text=Sign in",
        "text=Log in",
        "text=登录",
        "text=登入",
    ]
    return any(page.locator(sel).first.count() > 0 for sel in login_selectors)


def wait_for_manual_login_if_needed(ctx: ScrapeContext, tweet_url: str) -> None:
    page = ctx.page
    log("检测登录状态")
    page.wait_for_timeout(1200)

    if not needs_login(page):
        log("检测到已登录或可直接访问帖子")
        return

    print("\n[提示] 当前可能未登录 X/Twitter。")
    print("[提示] 请在浏览器中手动登录，登录完成并能看到帖子详情页后，在终端按回车继续...\n")
    input()

    # 用户登录后，强制回到目标帖页再抓取
    log("登录回车已收到，重新打开目标帖子页面")
    page.goto(tweet_url, wait_until="domcontentloaded", timeout=ctx.timeout_ms)


def open_url(ctx: ScrapeContext, url: str, title: str) -> None:
    log(f"正在打开页面（{title}）: {url}")
    ctx.page.goto(url, wait_until="domcontentloaded", timeout=ctx.timeout_ms)
    ensure_post_loaded(ctx)
    log(f"页面加载完成（{title}）")


def try_extract_from_locator_text(locator) -> Optional[str]:
    try:
        if locator.count() == 0:
            return None
        text = locator.first.inner_text().strip()
        return first_count_from_text(text)
    except PlaywrightError:
        return None


def try_extract_from_aria(page: Page, testids: list[str], keywords: list[str]) -> Optional[str]:
    for testid in testids:
        locator = page.locator(f"[data-testid='{testid}']")
        if locator.count() == 0:
            continue
        try:
            aria = locator.first.get_attribute("aria-label") or ""
            if aria:
                low = aria.lower()
                if any(k in low for k in keywords):
                    value = first_count_from_text(aria)
                    if value:
                        return value
            text_value = locator.first.inner_text().strip()
            value = first_count_from_text(text_value)
            if value:
                return value
        except PlaywrightError:
            continue
    return None


def extract_metric(page: Page, *, testids: list[str], keywords: list[str], text_patterns: list[str]) -> str:
    value = try_extract_from_aria(page, testids, keywords)
    if value:
        return value

    for pattern in text_patterns:
        locator = page.locator(f"span:has-text('{pattern}')")
        value = try_extract_from_locator_text(locator)
        if value:
            return value
        # 某些页面数字在父节点
        if locator.count() > 0:
            try:
                raw = locator.first.evaluate("el => (el.parentElement && el.parentElement.innerText) || el.innerText")
                value = first_count_from_text(raw or "")
                if value:
                    return value
            except PlaywrightError:
                pass

    return "N/A"


def extract_quotes(page: Page) -> str:
    link = page.locator("a[href*='/retweets/with_comments']")
    value = try_extract_from_locator_text(link)
    if value:
        return value

    return extract_metric(
        page,
        testids=["retweet", "unretweet"],
        keywords=["quote", "引用", "引用推文"],
        text_patterns=["Quote", "Quotes", "引用", "引用推文"],
    )


def collect_metrics(ctx: ScrapeContext) -> Metrics:
    page = ctx.page
    return Metrics(
        likes=extract_metric(
            page,
            testids=["like", "unlike"],
            keywords=["like", "喜欢", "赞"],
            text_patterns=["Like", "Likes", "喜欢", "赞"],
        ),
        replies=extract_metric(
            page,
            testids=["reply"],
            keywords=["repl", "回复", "回覆", "评论", "評論"],
            text_patterns=["Reply", "Replies", "回复", "回覆", "评论", "評論"],
        ),
        reposts=extract_metric(
            page,
            testids=["retweet", "unretweet"],
            keywords=["repost", "retweet", "转发", "轉發"],
            text_patterns=["Repost", "Reposts", "Retweet", "Retweets", "转发", "轉發"],
        ),
        quotes=extract_quotes(page),
        views=extract_metric(
            page,
            testids=["analytics"],
            keywords=["view", "views", "浏览", "瀏覽", "次查看"],
            text_patterns=["View", "Views", "浏览", "瀏覽", "次查看"],
        ),
    )


def detect_repost_and_original(page: Page, current_tweet_id: str) -> tuple[bool, Optional[str]]:
    article = page.locator("article[data-testid='tweet']").first
    if article.count() == 0:
        return False, None

    is_repost = False
    try:
        blob = article.inner_text().lower()
        if any(k in blob for k in ["reposted", "retweet", "转推", "轉推", "已转发", "已轉發"]):
            is_repost = True
    except PlaywrightError:
        pass

    # 从当前帖容器里抓取 status 链接 ID
    try:
        hrefs = article.evaluate_all(
            "els => els.flatMap(el => Array.from(el.querySelectorAll('a[href*=\"/status/\"]')).map(a => a.href))"
        )
    except PlaywrightError:
        hrefs = []

    ids: list[str] = []
    for href in hrefs:
        m = STATUS_URL_RE.search(str(href))
        if m:
            ids.append(m.group(1))

    for tid in ids:
        if tid != current_tweet_id:
            return True, tid

    return is_repost, None


def print_metrics_block(title: str, tweet_id: str, metrics: Metrics) -> None:
    print(f"\n{title}:")
    print(f"  Tweet ID: {tweet_id}")
    print(f"  Likes: {metrics.likes}")
    print(f"  Replies: {metrics.replies}")
    print(f"  Reposts: {metrics.reposts}")
    print(f"  Quotes: {metrics.quotes}")
    print(f"  Views: {metrics.views}")


def wait_before_close(page: Page, keep_open: bool, failed: bool) -> None:
    if keep_open:
        print("\n[调试] 浏览器将保持打开，按回车后关闭...")
        try:
            input()
        except EOFError:
            pass
        return

    if failed and not page.context.browser.is_connected():
        return

    if failed:
        print("\n[调试] 本次执行失败。浏览器将在 8 秒后关闭，便于查看页面状态。")
        page.wait_for_timeout(8000)


def run(url: str, user_data_dir: Path, headless: bool, timeout_s: int, keep_open: bool) -> int:
    input_url = normalize_url(url)
    tweet_id = extract_tweet_id(input_url)
    timeout_ms = max(timeout_s, 5) * 1000
    user_data_dir.mkdir(parents=True, exist_ok=True)

    failed = False

    log("正在启动浏览器（持久化上下文）")
    with sync_playwright() as p:
        context: BrowserContext = p.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            headless=headless,
            viewport={"width": 1400, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()
        ctx = ScrapeContext(page=page, timeout_ms=timeout_ms)

        try:
            open_url(ctx, input_url, "current post")
            wait_for_manual_login_if_needed(ctx, input_url)
            ensure_post_loaded(ctx)

            log("正在提取 current post metrics")
            current = collect_metrics(ctx)
            is_repost, original_id = detect_repost_and_original(page, tweet_id)

            print(f"\nInput URL: {url}")
            print(f"Tweet ID: {tweet_id}")
            print(f"Is repost: {'Yes' if is_repost else 'No'}")
            print_metrics_block("Current post metrics", tweet_id, current)

            if is_repost and original_id:
                log(f"检测到 repost，正在提取 original post metrics（ID: {original_id}）")
                original_url = f"https://x.com/i/status/{original_id}"
                open_url(ctx, original_url, "original post")
                original = collect_metrics(ctx)
                print_metrics_block("Original post metrics", original_id, original)
            elif is_repost:
                warn("检测到 repost，但未识别出 original post ID")
                print("\nOriginal post metrics: N/A (未成功识别 original post ID)")

            log("抓取成功")
            return 0

        except XBrowserMetricsError as exc:
            failed = True
            print(f"Error: {exc}")
            return 1
        except (PlaywrightTimeoutError, PlaywrightError) as exc:
            failed = True
            print(f"Error: Playwright failure - {exc}")
            return 1
        except KeyboardInterrupt:
            failed = True
            print("\nInterrupted by user.")
            return 1
        except Exception as exc:  # noqa: BLE001
            failed = True
            print(f"Error: Unexpected failure - {exc}")
            return 1
        finally:
            wait_before_close(page, keep_open=keep_open, failed=failed)
            time.sleep(0.5)
            context.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch X/Twitter metrics directly from webpage via Playwright.")
    parser.add_argument("url", help="X/Twitter status URL")
    parser.add_argument(
        "--user-data-dir",
        default=".x_browser_profile",
        help="Persistent browser profile directory (default: .x_browser_profile)",
    )
    parser.add_argument("--headless", action="store_true", help="Run in headless mode")
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Page timeout in seconds (default: 30)",
    )
    parser.add_argument(
        "--keep-open",
        action="store_true",
        help="Keep browser open until pressing Enter (debug friendly)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return run(
        url=args.url,
        user_data_dir=Path(args.user_data_dir),
        headless=args.headless,
        timeout_s=args.timeout,
        keep_open=args.keep_open,
    )


if __name__ == "__main__":
    sys.exit(main())
