from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from io import StringIO
from typing import List, Optional

import pandas as pd
import streamlit as st
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

URL_PATTERN = re.compile(r"^https?://t\.me/([A-Za-z0-9_]+)/([0-9]+)(?:\?.*)?$")


@dataclass
class ScrapeResult:
    input_url: str
    channel_name: Optional[str]
    message_id: Optional[str]
    views: Optional[str]
    status: str
    error: str


def parse_input_urls(raw_input: str) -> List[str]:
    urls = []
    for line in raw_input.splitlines():
        url = line.strip()
        if url:
            urls.append(url)
    return urls


def parse_telegram_url(url: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    match = URL_PATTERN.match(url)
    if not match:
        return None, None, "Invalid URL format. Expected https://t.me/<channel>/<message_id>."
    return match.group(1), match.group(2), None


def scrape_views_for_urls(urls: List[str], timeout_ms: int = 20000) -> List[ScrapeResult]:
    results: List[ScrapeResult] = []

    if not urls:
        return results

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        for url in urls:
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
                continue

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_selector(".tgme_widget_message_views", timeout=timeout_ms)
                views_text = page.locator(".tgme_widget_message_views").first.inner_text().strip()

                if not views_text:
                    raise ValueError("Views element found but text is empty.")

                results.append(
                    ScrapeResult(
                        input_url=url,
                        channel_name=channel_name,
                        message_id=message_id,
                        views=views_text,
                        status="success",
                        error="",
                    )
                )
            except PlaywrightTimeoutError:
                results.append(
                    ScrapeResult(
                        input_url=url,
                        channel_name=channel_name,
                        message_id=message_id,
                        views=None,
                        status="failed",
                        error="Timeout while loading message page or locating views.",
                    )
                )
            except Exception as exc:  # pylint: disable=broad-exception-caught
                results.append(
                    ScrapeResult(
                        input_url=url,
                        channel_name=channel_name,
                        message_id=message_id,
                        views=None,
                        status="failed",
                        error=str(exc),
                    )
                )

        context.close()
        browser.close()

    return results


def results_to_dataframe(results: List[ScrapeResult]) -> pd.DataFrame:
    return pd.DataFrame([asdict(item) for item in results])


def dataframe_to_csv(df: pd.DataFrame) -> str:
    buffer = StringIO()
    df.to_csv(buffer, index=False)
    return buffer.getvalue()


def main() -> None:
    st.set_page_config(page_title="Telegram Message Views Scraper", page_icon="📊", layout="wide")

    st.title("Telegram 公开消息浏览量抓取")
    st.caption("支持多行粘贴 t.me 公开消息链接，按输入顺序抓取每条消息的浏览量。")

    raw_urls = st.text_area(
        label="粘贴 Telegram 消息链接（每行一条）",
        placeholder="https://t.me/channelname/123\nhttps://t.me/anotherchannel/456",
        height=220,
    )

    start = st.button("Start", type="primary")

    if start:
        urls = parse_input_urls(raw_urls)

        if not urls:
            st.warning("请输入至少一条链接后再开始。")
            return

        with st.spinner("正在抓取，请稍候..."):
            results = scrape_views_for_urls(urls)
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


if __name__ == "__main__":
    main()
