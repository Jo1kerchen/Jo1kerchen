from __future__ import annotations

import argparse
import csv
import json
import struct
import zlib
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DATE_FMT = "%Y-%m-%d"
LIQUOR_CSV_PATH = Path("data/moutai_prices_0311_clean.csv")


@dataclass
class Point:
    date: date
    value: float


def _parse_date(s: str) -> date:
    s = str(s).strip()
    for fmt in ["%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d"]:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Invalid date: {s}")


def _http_get(url: str, timeout: int = 20) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def fetch_stock_data_from_eastmoney(start: date, end: date, out_csv: Path) -> None:
    params = {
        "secid": "1.600519",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "klt": "101",
        "fqt": "1",
        "beg": start.strftime("%Y%m%d"),
        "end": end.strftime("%Y%m%d"),
    }
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get?" + urlencode(params)
    try:
        payload = json.loads(_http_get(url))
    except Exception as e:
        raise RuntimeError(f"Failed to fetch stock data from Eastmoney: {e}") from e

    klines = (((payload or {}).get("data") or {}).get("klines")) or []
    if not klines:
        raise RuntimeError("Failed to fetch stock data: Eastmoney returned empty kline list")

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "close"])
        for row in klines:
            p = row.split(",")
            w.writerow([p[0], p[2]])


def load_stock_data(start: date, end: date, stock_csv: Path, auto_fetch: bool = True):
    import pandas as pd  # type: ignore

    if not stock_csv.exists():
        if not auto_fetch:
            raise FileNotFoundError(f"Stock CSV not found: {stock_csv}")
        fetch_stock_data_from_eastmoney(start, end, stock_csv)

    df = pd.read_csv(stock_csv)
    if "date" not in df.columns or "close" not in df.columns:
        raise ValueError(f"Stock CSV must include ['date','close'], got {list(df.columns)}")

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["date", "close"])  # type: ignore
    df = df[(df["date"] >= pd.Timestamp(start)) & (df["date"] <= pd.Timestamp(end))]
    df = df.sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    if df.empty:
        raise RuntimeError("Stock data is empty after filtering; please check csv or date range")
    return df[["date", "close"]]


def _series_stats(s):
    s = s.dropna()
    if s.empty:
        return "N/A", "N/A", "N/A"
    return f"{s.min():.2f}", f"{s.max():.2f}", f"{s.iloc[-1]:.2f}"


def load_liquor_data(start: date, end: date):
    import pandas as pd  # type: ignore

    liquor_csv = LIQUOR_CSV_PATH
    if not liquor_csv.exists():
        raise FileNotFoundError(f"Liquor CSV not found: {liquor_csv}")

    df = pd.read_csv(liquor_csv)
    required = ["date", "original_box_price", "bulk_price"]
    if list(df.columns) != required:
        raise ValueError(f"Liquor CSV columns must be exactly {required}, got {list(df.columns)}")

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["original_box_price"] = pd.to_numeric(df["original_box_price"], errors="coerce")
    df["bulk_price"] = pd.to_numeric(df["bulk_price"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date").drop_duplicates(subset=["date"], keep="last")
    df = df[(df["date"] >= pd.Timestamp(start)) & (df["date"] <= pd.Timestamp(end))].reset_index(drop=True)
    if df.empty:
        raise RuntimeError("Liquor CSV is empty after filtering; please check csv/date range")

    o_min, o_max, o_latest = _series_stats(df["original_box_price"])
    b_min, b_max, b_latest = _series_stats(df["bulk_price"])

    print(f"[DEBUG] liquor file path: {liquor_csv}")
    print(f"[DEBUG] liquor columns: {list(df.columns)}")
    print(f"[DEBUG] liquor rows: {len(df)}")
    print(f"[DEBUG] liquor date min/max: {df['date'].iloc[0].date()} / {df['date'].iloc[-1].date()}")
    print(f"[DEBUG] original_box_price min/max/latest: {o_min} / {o_max} / {o_latest}")
    print(f"[DEBUG] bulk_price min/max/latest: {b_min} / {b_max} / {b_latest}")

    latest_original = float(df["original_box_price"].dropna().iloc[-1])
    latest_bulk = float(df["bulk_price"].dropna().iloc[-1])
    if not (1300 <= latest_original <= 1800 and 1300 <= latest_bulk <= 1800):
        raise ValueError(
            f"Liquor latest values out of expected range. latest_original={latest_original}, latest_bulk={latest_bulk}."
        )
    if max(df["original_box_price"].max(skipna=True), df["bulk_price"].max(skipna=True)) > 4000:
        raise ValueError("Liquor price scale seems wrong (>4000). Please verify data/moutai_prices_0311_clean.csv")

    return df[["date", "original_box_price", "bulk_price"]]


def merge_and_align(stock_df, liquor_df):
    merged = stock_df.merge(liquor_df, on="date", how="left")
    merged["bulk_price"] = merged["bulk_price"].ffill()
    merged["original_box_price"] = merged["original_box_price"].ffill()

    merged["ema20"] = merged["close"].ewm(span=20, adjust=False).mean()
    merged["ema55"] = merged["close"].ewm(span=55, adjust=False).mean()
    merged["ema100"] = merged["close"].ewm(span=100, adjust=False).mean()
    merged["ema200"] = merged["close"].ewm(span=200, adjust=False).mean()
    return merged


def _compute_max_runup_and_drawdown(closes) -> tuple[float, float]:
    min_v = closes.iloc[0]
    max_up = -1.0
    run_max = closes.iloc[0]
    max_dd = 0.0
    for c in closes:
        if c < min_v:
            min_v = c
        up = c / min_v - 1
        if up > max_up:
            max_up = up
        if c > run_max:
            run_max = c
        dd = c / run_max - 1
        if dd < max_dd:
            max_dd = dd
    return max_up, max_dd


def plot_static_dual_axis_chart(merged_df, output_png: Path) -> None:
    try:
        import matplotlib.pyplot as plt  # type: ignore

        fig, ax1 = plt.subplots(figsize=(16, 8), dpi=180)
        ax2 = ax1.twinx()
        l1 = ax1.plot(merged_df["date"], merged_df["close"], color="#1565c0", linewidth=1.8, label="贵州茅台股价")
        l2 = ax2.plot(merged_df["date"], merged_df["bulk_price"], color="#c62828", linewidth=1.8, label="飞天茅台53度当年散装参考价")
        ax1.set_title("贵州茅台股价与飞天茅台53度散瓶市场参考价走势对比\n时间范围：2018-01-01 至今（按A股交易日对齐）")
        ax1.set_xlabel("日期")
        ax1.set_ylabel("贵州茅台收盘价（元）", color="#1565c0")
        ax2.set_ylabel("飞天茅台53度参考价（元/瓶）", color="#c62828")
        ax1.grid(True, linestyle="--", alpha=0.15)
        lines = l1 + l2
        ax1.legend(lines, [x.get_label() for x in lines], loc="upper left")
        fig.tight_layout()
        output_png.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_png)
        plt.close(fig)
        return
    except Exception:
        print("[WARN] matplotlib not found, using built-in PNG renderer.")

    w, h = 1800, 900
    img = bytearray([255] * (w * h * 3))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack("!I", len(data)) + tag + data + struct.pack("!I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + bytes(img[y * w * 3 : (y + 1) * w * 3]) for y in range(h))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack("!IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    output_png.parent.mkdir(parents=True, exist_ok=True)
    output_png.write_bytes(png)


def plot_interactive_dual_axis_chart(merged_df, output_html: Path) -> None:
    import plotly.graph_objects as go  # type: ignore
    import plotly.io as pio  # type: ignore

    max_up, max_dd = _compute_max_runup_and_drawdown(merged_df["close"])

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=merged_df["date"],
            y=merged_df["close"],
            name="贵州茅台股价",
            mode="lines",
            line=dict(color="#1565c0", width=2.2),
            yaxis="y1",
            hovertemplate="日期：%{x|%Y年%m月%d日}<br>贵州茅台股价：%{y:.2f} 元<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=merged_df["date"],
            y=merged_df["bulk_price"],
            name="飞天茅台53度当年散装参考价",
            mode="lines",
            line=dict(color="#c62828", width=2.2),
            yaxis="y2",
            hovertemplate="日期：%{x|%Y年%m月%d日}<br>飞天茅台53度当年散装参考价：%{y:.2f} 元/瓶<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=merged_df["date"],
            y=merged_df["original_box_price"],
            name="飞天茅台53度当年原装参考价",
            mode="lines",
            line=dict(color="#ef6c00", width=1.8, dash="dot"),
            yaxis="y2",
            visible="legendonly",
            hovertemplate="日期：%{x|%Y年%m月%d日}<br>飞天茅台53度当年原装参考价：%{y:.2f} 元/瓶<extra></extra>",
        )
    )

    for name, col, color in [
        ("EMA20", "ema20", "#42a5f5"),
        ("EMA55", "ema55", "#26a69a"),
        ("EMA100", "ema100", "#ab47bc"),
        ("EMA200", "ema200", "#8d6e63"),
    ]:
        fig.add_trace(
            go.Scatter(
                x=merged_df["date"],
                y=merged_df[col],
                name=name,
                mode="lines",
                line=dict(color=color, width=1.1),
                yaxis="y1",
                visible="legendonly",
                hovertemplate=f"日期：%{{x|%Y年%m月%d日}}<br>{name}：%{{y:.2f}}<extra></extra>",
            )
        )

    fig.update_layout(
        title="贵州茅台股价与飞天茅台53度散瓶市场参考价走势对比<br><sup>时间范围：2018-01-01 至今（按A股交易日对齐）｜酒价数据来源：用户提供的批发参考价整理表（CSV）</sup>",
        xaxis=dict(title="日期", type="date", tickformat="%Y年%m月", hoverformat="%Y年%m月%d日", showgrid=False),
        yaxis=dict(title="贵州茅台收盘价（元）", side="left", showgrid=False),
        yaxis2=dict(title="飞天茅台53度参考价（元/瓶）", overlaying="y", side="right", showgrid=False),
        hovermode="x unified",
        template="plotly_white",
        plot_bgcolor="white",
        paper_bgcolor="white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(l=70, r=70, t=110, b=60),
    )

    fig.add_annotation(
        x=0.985,
        y=0.985,
        xref="paper",
        yref="paper",
        showarrow=False,
        text=f"最大涨幅：+{max_up * 100:.2f}%<br>最大回撤：{max_dd * 100:.2f}%",
        xanchor="right",
        yanchor="top",
        align="right",
        font=dict(size=12, color="#333"),
        bgcolor="rgba(255,255,255,0.88)",
        bordercolor="rgba(0,0,0,0.2)",
        borderwidth=1,
    )

    latest_idx = len(merged_df) - 1
    fig.add_annotation(
        x=merged_df.iloc[latest_idx]["date"],
        y=float(merged_df.iloc[latest_idx]["close"]),
        text=f"最新股价: {float(merged_df.iloc[latest_idx]['close']):.2f}",
        showarrow=True,
        arrowhead=2,
        ax=22,
        ay=-28,
        yref="y",
        font=dict(color="#1565c0", size=12),
        bgcolor="rgba(255,255,255,0.75)",
    )
    latest_bulk = merged_df["bulk_price"].dropna()
    if not latest_bulk.empty:
        latest_bulk_idx = latest_bulk.index[-1]
        fig.add_annotation(
            x=merged_df.loc[latest_bulk_idx, "date"],
            y=float(merged_df.loc[latest_bulk_idx, "bulk_price"]),
            text=f"最新散装价: {float(merged_df.loc[latest_bulk_idx, 'bulk_price']):.2f}",
            showarrow=True,
            arrowhead=2,
            ax=22,
            ay=28,
            yref="y2",
            font=dict(color="#c62828", size=12),
            bgcolor="rgba(255,255,255,0.75)",
        )

    output_html.parent.mkdir(parents=True, exist_ok=True)
    pio.write_html(fig, file=str(output_html), include_plotlyjs=True, full_html=True)


def save_merged_csv(merged_df, output_csv: Path) -> None:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    merged_df[[
        "date",
        "close",
        "bulk_price",
        "original_box_price",
        "ema20",
        "ema55",
        "ema100",
        "ema200",
    ]].to_csv(output_csv, index=False)


def _print_merge_and_plot_debug(stock_df, merged_df) -> None:
    print(f"[DEBUG] stock rows: {len(stock_df)}")
    print(f"[DEBUG] stock date min/max: {stock_df['date'].iloc[0].date()} / {stock_df['date'].iloc[-1].date()}")
    print(f"[DEBUG] merged rows: {len(merged_df)}")
    print(f"[DEBUG] merged date min/max: {merged_df['date'].iloc[0].date()} / {merged_df['date'].iloc[-1].date()}")

    b = merged_df["bulk_price"].dropna()
    o = merged_df["original_box_price"].dropna()
    b_min = "N/A" if b.empty else f"{b.min():.2f}"
    b_max = "N/A" if b.empty else f"{b.max():.2f}"
    b_latest = "N/A" if b.empty else f"{b.iloc[-1]:.2f}"
    o_min = "N/A" if o.empty else f"{o.min():.2f}"
    o_max = "N/A" if o.empty else f"{o.max():.2f}"
    o_latest = "N/A" if o.empty else f"{o.iloc[-1]:.2f}"

    print(f"[DEBUG] plotted bulk_price min/max/latest: {b_min} / {b_max} / {b_latest}")
    print(f"[DEBUG] plotted original_box_price min/max/latest: {o_min} / {o_max} / {o_latest}")


def main() -> None:
    parser = argparse.ArgumentParser(description="贵州茅台股价 vs 飞天茅台53度散瓶参考价 双轴图")
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--end", default=date.today().strftime(DATE_FMT))
    parser.add_argument("--stock-csv", type=Path, default=Path("data/stock_prices_auto.csv"))
    parser.add_argument("--output-png", type=Path, default=Path("output/moutai_stock_vs_liquor_dual_axis.png"))
    parser.add_argument("--output-html", type=Path, default=Path("output/moutai_stock_vs_liquor_interactive.html"))
    parser.add_argument("--output-merged-csv", type=Path, default=Path("output/moutai_stock_vs_liquor_merged.csv"))
    parser.add_argument("--no-auto-fetch-stock", action="store_true", help="Do not auto-fetch stock data if stock csv missing")
    args = parser.parse_args()

    start = _parse_date(args.start)
    end = _parse_date(args.end)

    stock_df = load_stock_data(start, end, args.stock_csv, auto_fetch=not args.no_auto_fetch_stock)
    liquor_df = load_liquor_data(start, end)
    merged_df = merge_and_align(stock_df, liquor_df)
    _print_merge_and_plot_debug(stock_df, merged_df)

    save_merged_csv(merged_df, args.output_merged_csv)
    plot_static_dual_axis_chart(merged_df, args.output_png)
    plot_interactive_dual_axis_chart(merged_df, args.output_html)

    print(f"[OK] merged csv: {args.output_merged_csv}")
    print(f"[OK] chart png: {args.output_png}")
    print(f"[OK] interactive html: {args.output_html}")


if __name__ == "__main__":
    main()
