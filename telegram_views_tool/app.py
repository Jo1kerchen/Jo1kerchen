from __future__ import annotations

import argparse
import re
from dataclasses import asdict, dataclass, field
from io import StringIO
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st
from playwright.sync_api import Frame
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
NUMBER_PATTERN = re.compile(r"\b\d{1,3}(?:,\d{3})*(?:\.\d+)?[KkMm]?\b")


@dataclass
class ScrapeResult:
    input_url: str
    channel_name: Optional[str]
    message_id: Optional[str]
    views: Optional[str]
    status: str
    error: str


@dataclass
class FrameDebug:
    index: int
    url: str
    title: str
    text_head: str
    hit_message_candidate: bool
    hit_views_candidate: bool


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
    frame_count: int = 0
    frame_debug: List[FrameDebug] = field(default_factory=list)
    matched_view_frame: str = ""
    matched_message_frame: str = ""
    events: List[str] = field(default_factory=list)


def parse_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    parser.add_argument("--timeout", type=int, default=30, help="Per URL timeout in seconds (default: 30)")
    args, _ = parser.parse_known_args()
    return args


def parse_input_urls(raw_input: str) -> List[str]:
    return [line.strip() for line in raw_input.splitlines() if line.strip()]


def parse_telegram_url(url: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    match = URL_PATTERN.match(url)
    if not match:
        return None, None, "Invalid URL format. Expected https://t.me/<channel>/<message_id>."
    return match.group(1), match.group(2), None


def _safe_text_head(frame_or_page: Any, timeout_ms: int = 3000) -> str:
    try:
        text = frame_or_page.locator("body").inner_text(timeout=timeout_ms)
        return (text or "")[:1000]
    except Exception:  # pylint: disable=broad-exception-caught
        return ""


def _safe_title(frame_or_page: Any) -> str:
    try:
        return frame_or_page.title() or ""
    except Exception:  # pylint: disable=broad-exception-caught
        return ""


def _safe_page_snapshot(page: Any, debug_info: DebugInfo) -> None:
    try:
        debug_info.final_url = page.url
    except Exception as exc:  # pylint: disable=broad-exception-caught
        debug_info.events.append(f"Failed to read page.url: {exc}")

    debug_info.title = _safe_title(page)

    try:
        text_content = page.locator("body").inner_text(timeout=3000)
        debug_info.body_inner_text_head = (text_content or "")[:2000]
    except Exception as exc:  # pylint: disable=broad-exception-caught
        debug_info.events.append(f"Failed to read body.innerText: {exc}")


def _collect_main_debug_details(page: Any, debug_info: DebugInfo) -> None:
    _safe_page_snapshot(page, debug_info)

    try:
        visible_text = page.evaluate(
            """
            () => {
                if (!document.body) return '';
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
                if (!document.body) return [];
                const result = [];
                const lowerKeywords = keywords.map(k => k.toLowerCase());
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                while (walker.nextNode()) {
                    const text = (walker.currentNode.textContent || '').trim();
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
                if (!document.body) return [];
                const selectors = [
                    '.tgme_widget_message_wrap', '.tgme_widget_message', '.tgme_widget_message_bubble',
                    '.tgme_channel_info', '.tgme_page', '.tgme_page_widget',
                    '[data-telegram-post]', 'iframe', 'article', 'main', '[data-post]'
                ];
                const result = [];
                for (const selector of selectors) {
                    const nodes = Array.from(document.querySelectorAll(selector));
                    for (const node of nodes) {
                        const text = (node.innerText || node.textContent || '').trim();
                        const post = node.getAttribute ? (node.getAttribute('data-telegram-post') || '') : '';
                        const src = node.getAttribute ? (node.getAttribute('src') || '') : '';
                        const meta = [post ? `data-telegram-post=${post}` : '', src ? `src=${src}` : ''].filter(Boolean).join(' ');
                        if (text || meta) {
                            result.push(`[${selector}] ${meta} ${(text || '').slice(0, 300)}`.trim());
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
                if (!document.body) return [];
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

    combined_text = "\n".join([
        debug_info.visible_text_head,
        debug_info.body_inner_text_head,
        "\n".join(debug_info.keyword_text_nodes),
        "\n".join(debug_info.short_visible_texts),
    ])
    lowered = combined_text.lower()
    debug_info.presence_markers = {marker: marker.lower() in lowered for marker in PRESENCE_MARKERS}


def _extract_views_from_text(text: str) -> Optional[str]:
    if not text:
        return None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for idx, line in enumerate(lines):
        lowered = line.lower()
        if "view" in lowered or "eye" in lowered or "浏览" in line or "查看" in line:
            number = NUMBER_PATTERN.search(line)
            if number:
                return number.group(0)
            if idx + 1 < len(lines):
                next_number = NUMBER_PATTERN.search(lines[idx + 1])
                if next_number:
                    return next_number.group(0)

    generic = NUMBER_PATTERN.findall(text)
    if generic:
        return generic[-1]
    return None


def _extract_message_meta_from_context(context_obj: Any) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    selectors = [
        ".tgme_widget_message_views",
        ".tgme_widget_message_info_views",
        "span.tgme_widget_message_views",
    ]
    for selector in selectors:
        try:
            locator = context_obj.locator(selector)
            if locator.count() > 0:
                txt = locator.first.inner_text().strip()
                if txt:
                    parsed = _extract_views_from_text(txt)
                    if parsed:
                        try:
                            channel_post = context_obj.locator("[data-post]").first.get_attribute("data-post")
                        except Exception:  # pylint: disable=broad-exception-caught
                            channel_post = None
                        if channel_post and "/" in channel_post:
                            c_name, m_id = channel_post.split("/", 1)
                            return c_name, m_id, parsed
                        return None, None, parsed
        except Exception:  # pylint: disable=broad-exception-caught
            continue

    try:
        data_post = context_obj.locator("[data-telegram-post]").first.get_attribute("data-telegram-post")
    except Exception:  # pylint: disable=broad-exception-caught
        data_post = None

    text_head = _safe_text_head(context_obj)
    views = _extract_views_from_text(text_head)

    channel_name: Optional[str] = None
    message_id: Optional[str] = None
    if data_post and "/" in data_post:
        channel_name, message_id = data_post.split("/", 1)

    return channel_name, message_id, views


def _collect_frames_debug(page: Any, debug_info: DebugInfo) -> None:
    frames = page.frames
    debug_info.frame_count = len(frames)
    debug_info.frame_debug = []

    for index, frame in enumerate(frames):
        text_head = _safe_text_head(frame)
        title = _safe_title(frame)
        lowered = text_head.lower()
        hit_message = any(token in lowered for token in ["telegram", "view in", "channel", "tgme", "message"])
        hit_views = any(token in lowered for token in ["views", "view", "eye", "浏览", "查看"]) and bool(
            NUMBER_PATTERN.search(text_head)
        )
        debug_info.frame_debug.append(
            FrameDebug(
                index=index,
                url=frame.url,
                title=title,
                text_head=text_head,
                hit_message_candidate=hit_message,
                hit_views_candidate=hit_views,
            )
        )


def _extract_from_frames(page: Any, debug_info: Optional[DebugInfo]) -> Tuple[Optional[str], Optional[str], Optional[str], str]:
    frames: List[Frame] = page.frames
    for index, frame in enumerate(frames):
        c_name, m_id, views = _extract_message_meta_from_context(frame)
        if views:
            frame_id = f"frame[{index}] {frame.url}"
            if debug_info is not None:
                debug_info.matched_view_frame = frame_id
                debug_info.matched_message_frame = frame_id
            return c_name, m_id, views, frame_id
    return None, None, None, ""


def _extract_from_main_page(page: Any, fallback_channel: Optional[str], fallback_message_id: Optional[str]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    c_name, m_id, views = _extract_message_meta_from_context(page)

    if (not c_name or not m_id) and (fallback_channel and fallback_message_id):
        c_name = c_name or fallback_channel
        m_id = m_id or fallback_message_id

    if (not c_name or not m_id):
        try:
            data_post = page.locator("[data-telegram-post]").first.get_attribute("data-telegram-post")
            if data_post and "/" in data_post:
                c_tmp, m_tmp = data_post.split("/", 1)
                c_name = c_name or c_tmp
                m_id = m_id or m_tmp
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    return c_name, m_id, views


def scrape_views_for_urls(
    urls: List[str], timeout_ms: int = 30000, debug: bool = False
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
            input_channel_name, input_message_id, parse_error = parse_telegram_url(url)

            if parse_error:
                results.append(
                    ScrapeResult(url, input_channel_name, input_message_id, None, "failed", parse_error)
                )
                if debug:
                    debug_info.events.append(parse_error)
                    debug_infos.append(debug_info)
                continue

            channel_name = input_channel_name
            message_id = input_message_id
            views: Optional[str] = None
            status = "failed"
            error_message = ""

            try:
                debug_info.events.append(f"goto(wait_until=networkidle, timeout={timeout_ms}ms)")
                page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                debug_info.events.append("goto completed")

                try:
                    page.wait_for_selector("body", state="visible", timeout=min(8000, timeout_ms))
                    debug_info.events.append("body visible")
                except Exception as body_exc:  # pylint: disable=broad-exception-caught
                    debug_info.events.append(f"body visibility wait failed: {body_exc}")

                _collect_main_debug_details(page, debug_info)
                _collect_frames_debug(page, debug_info)

                c_main, m_main, v_main = _extract_from_main_page(page, input_channel_name, input_message_id)
                if c_main:
                    channel_name = c_main
                if m_main:
                    message_id = m_main

                c_frame, m_frame, v_frame, frame_id = _extract_from_frames(page, debug_info if debug else None)

                if v_frame:
                    views = v_frame
                    if c_frame:
                        channel_name = c_frame
                    if m_frame:
                        message_id = m_frame
                    debug_info.events.append(f"views extracted from {frame_id}")
                else:
                    views = v_main
                    if views:
                        debug_info.events.append("views extracted from main page")

                if views:
                    status = "success"
                else:
                    error_message = "Views not found in main DOM or frames. Please inspect debug output."

            except PlaywrightTimeoutError as timeout_exc:
                error_message = f"Timeout while loading page: {timeout_exc}"
                debug_info.events.append(error_message)
                _safe_page_snapshot(page, debug_info)
                _collect_frames_debug(page, debug_info)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                error_message = str(exc)
                debug_info.events.append(f"Unhandled exception: {exc}")
                _safe_page_snapshot(page, debug_info)
                _collect_frames_debug(page, debug_info)

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
            st.markdown(f"**Frame 数量:** `{item.frame_count}`")
            st.markdown(f"**命中消息 frame:** `{item.matched_message_frame or '<none>'}`")
            st.markdown(f"**命中 views frame:** `{item.matched_view_frame or '<none>'}`")

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

            st.markdown("**Frame 调试详情**")
            for frame_item in item.frame_debug:
                st.markdown(
                    f"- `frame[{frame_item.index}]` url=`{frame_item.url}` | title=`{frame_item.title}` | "
                    f"message_hit={frame_item.hit_message_candidate} | views_hit={frame_item.hit_views_candidate}"
                )
                st.code(frame_item.text_head or "<empty>")

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
            "Timeout (seconds)", min_value=5, max_value=180, value=max(5, args.timeout), step=5
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
