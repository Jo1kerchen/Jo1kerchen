#!/usr/bin/env python3
"""Streamlit UI for X/Twitter metrics scraping."""

from __future__ import annotations

import streamlit as st

from x_metrics_browser import CsvRow, parse_multiline_urls, process_urls, rows_to_csv_text

st.set_page_config(page_title="X Metrics Scraper", layout="wide")
st.title("X/Twitter Metrics Scraper")
st.caption("Paste one URL per line, then click Start.")

urls_text = st.text_area(
    "Input URLs (one per line)",
    height=220,
    placeholder="https://x.com/aaa/status/123\nhttps://twitter.com/bbb/status/456",
)

col1, col2 = st.columns(2)
with col1:
    headless = st.checkbox("Headless", value=True)
with col2:
    debug_views = st.checkbox("Debug views", value=False)

timeout_s = st.number_input("Timeout (seconds)", min_value=5, max_value=120, value=30, step=5)

if st.button("Start scraping", type="primary"):
    urls = parse_multiline_urls(urls_text)
    if not urls:
        st.warning("Please provide at least one valid URL line.")
    else:
        progress_text = st.empty()
        progress_bar = st.progress(0)

        def on_progress(i: int, total: int, url: str) -> None:
            progress_text.info(f"[{i}/{total}] Processing: {url}")
            progress_bar.progress(int(i / total * 100))

        with st.spinner("Scraping in progress..."):
            rows = process_urls(
                urls,
                headless=headless,
                timeout_s=int(timeout_s),
                debug_views=debug_views,
                progress_callback=on_progress,
            )

        progress_text.success("Scraping finished.")
        progress_bar.progress(100)

        table_rows = [row.__dict__ for row in rows]
        st.subheader("Results")
        st.dataframe(table_rows, use_container_width=True)

        success_count = sum(1 for row in rows if row.status == "success")
        failed_count = len(rows) - success_count
        st.write(f"Success: **{success_count}** | Failed: **{failed_count}**")

        csv_text = rows_to_csv_text(rows)
        st.download_button(
            label="Download CSV",
            data=csv_text,
            file_name="results.csv",
            mime="text/csv",
        )
