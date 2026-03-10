from __future__ import annotations

import argparse
import re
from dataclasses import asdict, dataclass, field
from io import StringIO
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

URL_PATTERN = re.compile(r"^https?://t\.me/([A-Za-z0-9_]+)/([0-9]+)(?:\?.*)?$")
KEYWORDS = ["view", "views", "查看", "浏览", "eye", "telegram", "preview", "join", "open"]
PRESENCE_MARKERS = [
    "View in Telegram",
    "Open channel",
    "Join channel",
    "Preview channel",
    "Open App",
    "在 Telegram 中查看",
    "加入频道",
]


@dataclass
class ScrapeResult:
    input_url: str
    channel_name: Optional[str]
    message_id: Optional[str]
    views: Optional[str]
    status: str
    error: str


@dataclass
class DebugInfo:
    input_url: str
    final_url: str = ""
    title: str = ""
    visible_text_head: str = ""
    body_inner_text_head: str = ""
    keyword_text_nodes: List[str] = field(default_factory=list)
    message_container_candidates: List[str] = field(default_factory=list)
    short_visible_texts: List[str] = field(default_factory=list)
    presence_markers: Dict[str, bool] = field(default_factory=dict)
    events: List[str] = field(default_factory=list)


def parse_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Per URL timeout in seconds (default: 30)",
    )
    args, _ = parser.parse_known_args()
    return args


def parse_input_urls(raw_input: str) -> List[str]:
    return [line.strip() for line in raw_input.splitlines() if line.strip()]


def parse_telegram_url(url: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    match = URL_PATTERN.match(url)
    if not match:
        return None, None, "Invalid URL format. Expected https://t.me/<channel>/<message_id>."
    return match.group(1), match.group(2), None


def _safe_page_snapshot(page: Any, debug_info: DebugInfo) -> None:
    try:
        debug_info.final_url = page.url
    except Exception as exc:  # pylint: disable=broad-exception-caught
        debug_info.events.append(f"Failed to read page.url: {exc}")

    try:
        debug_info.title = page.title()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        debug_info.events.append(f"Failed to read page.title(): {exc}")

    try:
        text_content = page.locator("body").inner_text(timeout=3000)
        debug_info.body_inner_text_head = text_content[:2000]
    except Exception as exc:  # pylint: disable=broad-exception-caught
        debug_info.events.append(f"Failed to read body.innerText: {exc}")


def _collect_debug_details(page: Any, debug_info: DebugInfo) -> None:
    _safe_page_snapshot(page, debug_info)

    try:
        visible_text = page.evaluate(
            """
            () => {
                const nodes = [];
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                while (walker.nextNode()) {
                    const node = walker.currentNode;
                    const text = (node.textContent || '').trim();
                    if (!text) continue;
                    const parent = node.parentElement;
                    if (!parent) continue;
                    const style = window.getComputedStyle(parent);
                    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;
                    const rect = parent.getBoundingClientRect();
                    if (rect.width === 0 || rect.height === 0) continue;
                    nodes.push(text);
                }
                return nodes.join(' ');
            }
            """
        )
        debug_info.visible_text_head = (visible_text or "")[:2000]
    except Exception as exc:  # pylint: disable=broad-exception-caught
        debug_info.events.append(f"Failed to collect visible text: {exc}")

    try:
        keyword_nodes = page.evaluate(
            """
            (keywords) => {
                const result = [];
                const lowerKeywords = keywords.map(k => k.toLowerCase());
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                while (walker.nextNode()) {
                    const node = walker.currentNode;
                    const text = (node.textContent || '').trim();
                    if (!text) continue;
                    const lowered = text.toLowerCase();
                    if (lowerKeywords.some(k => lowered.includes(k))) {
                        result.push(text.slice(0, 200));
                        if (result.length >= 120) break;
                    }
                }
                return result;
            }
            """,
            KEYWORDS,
        )
        debug_info.keyword_text_nodes = keyword_nodes or []
    except Exception as exc:  # pylint: disable=broad-exception-caught
        debug_info.events.append(f"Failed to collect keyword text nodes: {exc}")

    try:
        container_candidates = page.evaluate(
            """
            () => {
                const selectors = [
                    '.tgme_widget_message_wrap',
                    '.tgme_widget_message',
                    '.tgme_widget_message_bubble',
                    '.tgme_channel_info',
                    '.tgme_page',
                    '.tgme_page_widget',
                    'article',
                    'main',
                    '[data-post]',
                ];
                const result = [];
                for (const selector of selectors) {
                    const nodes = Array.from(document.querySelectorAll(selector));
                    for (const node of nodes) {
                        const text = (node.innerText || '').trim();
                        if (text) {
                            result.push(`[${selector}] ${text.slice(0, 300)}`);
                        }
                        if (result.length >= 80) return result;
                    }
                }
                return result;
            }
            """
        )
        debug_info.message_container_candidates = container_candidates or []
    except Exception as exc:  # pylint: disable=broad-exception-caught
        debug_info.events.append(f"Failed to collect message container candidates: {exc}")

    try:
        short_texts = page.evaluate(
            """
            () => {
                const result = [];
                const nodes = Array.from(document.querySelectorAll('a,button,div,span'));
                for (const node of nodes) {
                    const text = (node.innerText || '').trim().replace(/\s+/g, ' ');
                    if (!text || text.length > 80) continue;
                    const style = window.getComputedStyle(node);
                    if (style.display === 'none' || style.visibility === 'hidden') continue;
                    const rect = node.getBoundingClientRect();
                    if (rect.width === 0 || rect.height === 0) continue;
                    result.push(`${node.tagName.toLowerCase()}: ${text}`);
                    if (result.length >= 300) break;
                }
                return result;
            }
            """
        )
        debug_info.short_visible_texts = short_texts or []
    except Exception as exc:  # pylint: disable=broad-exception-caught
        debug_info.events.append(f"Failed to collect short visible texts: {exc}")

    combined_text = "\n".join(
        [
            debug_info.visible_text_head,
            debug_info.body_inner_text_head,
            "\n".join(debug_info.keyword_text_nodes),
            "\n".join(debug_info.short_visible_texts),
        ]
    )
    lowered = combined_text.lower()
    debug_info.presence_markers = {marker: marker.lower() in lowered for marker in PRESENCE_MARKERS}


def _extract_views(page: Any) -> Optional[str]:
    selectors = [
        ".tgme_widget_message_views",
        ".tgme_widget_message_info_views",
        "span.tgme_widget_message_views",
    ]
    for selector in selectors:
        locator = page.locator(selector)
        if locator.count() > 0:
            text = locator.first.inner_text().strip()
            if text:
                return text
    return None


def scrape_views_for_urls(
    urls: List[str],
    timeout_ms: int = 30000,
    debug: bool = False,
) -> Tuple[List[ScrapeResult], List[DebugInfo]]:
    results: List[ScrapeResult] = []
    debug_infos: List[DebugInfo] = []

    if not urls:
        return results, debug_infos

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        for url in urls:
            debug_info = DebugInfo(input_url=url)
            channel_name, message_id, parse_error = parse_telegram_url(url)

            if parse_error:
                results.append(
                    ScrapeResult(
                        input_url=url,
                        channel_name=channel_name,
                        message_id=message_id,
                        views=None,
                        status="failed",
                        error=parse_error,
                    )
                )
                if debug:
                    debug_info.events.append(parse_error)
                    debug_infos.append(debug_info)
                continue

            views: Optional[str] = None
            error_message = ""

            try:
                debug_info.events.append(f"goto(wait_until=networkidle, timeout={timeout_ms}ms)")
                page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                debug_info.events.append("goto completed")

                try:
                    page.wait_for_selector("body", state="visible", timeout=min(6000, timeout_ms))
                    debug_info.events.append("body visible")
                except Exception as body_exc:  # pylint: disable=broad-exception-caught
                    debug_info.events.append(f"body visibility wait failed: {body_exc}")

                _collect_debug_details(page, debug_info)
                views = _extract_views(page)

                if views:
                    status = "success"
                else:
                    status = "failed"
                    error_message = "Views not found on page. Please inspect debug output."

            except PlaywrightTimeoutError as timeout_exc:
                status = "failed"
                error_message = f"Timeout while loading page: {timeout_exc}"
                debug_info.events.append(error_message)
                _safe_page_snapshot(page, debug_info)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                status = "failed"
                error_message = str(exc)
                debug_info.events.append(f"Unhandled exception: {exc}")
                _safe_page_snapshot(page, debug_info)

            if debug and not debug_info.visible_text_head and not debug_info.body_inner_text_head:
                _collect_debug_details(page, debug_info)

            results.append(
                ScrapeResult(
                    input_url=url,
                    channel_name=channel_name,
                    message_id=message_id,
                    views=views,
                    status=status,
                    error=error_message,
                )
            )
            if debug:
                debug_infos.append(debug_info)

        context.close()
        browser.close()

    return results, debug_infos


def results_to_dataframe(results: List[ScrapeResult]) -> pd.DataFrame:
    return pd.DataFrame([asdict(item) for item in results])


def dataframe_to_csv(df: pd.DataFrame) -> str:
    buffer = StringIO()
    df.to_csv(buffer, index=False)
    return buffer.getvalue()


def render_debug_info(debug_infos: List[DebugInfo]) -> None:
    st.subheader("Debug 输出")
    for index, item in enumerate(debug_infos, start=1):
        label = f"#{index} {item.input_url}"
        with st.expander(label, expanded=False):
            st.markdown(f"**Raw input URL:** `{item.input_url}`")
            st.markdown(f"**Final page.url:** `{item.final_url}`")
            st.markdown(f"**Page title:** `{item.title}`")

            st.markdown("**页面前 2000 个可见字符文本**")
            st.code(item.visible_text_head or "<empty>")

            st.markdown("**body.innerText 前 2000 个字符**")
            st.code(item.body_inner_text_head or "<empty>")

            st.markdown("**关键词命中文本节点**")
            st.write(item.keyword_text_nodes or ["<none>"])

            st.markdown("**消息容器候选节点文本**")
            st.write(item.message_container_candidates or ["<none>"])

            st.markdown("**a/button/div/span 中较短可见文本**")
            st.write(item.short_visible_texts or ["<none>"])

            st.markdown("**关键 UI 文案是否出现**")
            st.json(item.presence_markers)

            st.markdown("**调试事件日志**")
            st.write(item.events or ["<none>"])


def main() -> None:
    args = parse_cli_args()
    st.set_page_config(page_title="Telegram Message Views Scraper", page_icon="📊", layout="wide")

    st.title("Telegram 公开消息浏览量抓取")
    st.caption("支持多行粘贴 t.me 公开消息链接，按输入顺序抓取每条消息的浏览量。")

    raw_urls = st.text_area(
        label="粘贴 Telegram 消息链接（每行一条）",
        placeholder="https://t.me/channelname/123\nhttps://t.me/anotherchannel/456",
        height=220,
    )

    col1, col2 = st.columns([1, 1])
    with col1:
        debug_mode = st.checkbox("Debug", value=args.debug)
    with col2:
        timeout_seconds = st.number_input(
            "Timeout (seconds)",
            min_value=5,
            max_value=180,
            value=max(5, args.timeout),
            step=5,
        )

    start = st.button("Start", type="primary")

    if start:
        urls = parse_input_urls(raw_urls)
        if not urls:
            st.warning("请输入至少一条链接后再开始。")
            return

        with st.spinner("正在抓取，请稍候..."):
            results, debug_infos = scrape_views_for_urls(
                urls=urls,
                timeout_ms=int(timeout_seconds * 1000),
                debug=debug_mode,
            )
            df = results_to_dataframe(results)

        st.subheader("抓取结果")
        st.dataframe(df, use_container_width=True)

        csv_content = dataframe_to_csv(df)
        st.download_button(
            label="Download CSV",
            data=csv_content,
            file_name="telegram_views_results.csv",
            mime="text/csv",
        )

        if debug_mode:
            render_debug_info(debug_infos)


if __name__ == "__main__":
    main()
