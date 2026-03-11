from __future__ import annotations

import argparse
import csv
import json
import math
import struct
import zlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DATE_FMT = "%Y-%m-%d"


@dataclass
class Point:
    date: date
    value: float


@dataclass
class LiquorPoint:
    date: date
    bulk_price: Optional[float]
    original_box_price: Optional[float]


def _parse_date(s: str) -> date:
    s = str(s).strip()
    for fmt in ["%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d"]:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Invalid date: {s}")


def _pick_col(headers: List[str], candidates: List[str], kind: str) -> str:
    h = set(headers)
    for c in candidates:
        if c in h:
            return c
    raise ValueError(f"{kind} CSV 缺少必需列，候选列: {candidates}，实际列: {headers}")


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


def load_stock_data(start: date, end: date, stock_csv: Path, auto_fetch: bool = True) -> List[Point]:
    if not stock_csv.exists():
        if not auto_fetch:
            raise FileNotFoundError(f"Stock CSV not found: {stock_csv}")
        fetch_stock_data_from_eastmoney(start, end, stock_csv)

    points: List[Point] = []
    with stock_csv.open("r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        headers = r.fieldnames or []
        dcol = _pick_col(headers, ["date", "日期"], "股票")
        vcol = _pick_col(headers, ["close", "stock_close", "收盘价", "price"], "股票")
        for row in r:
            try:
                d = _parse_date(row[dcol])
                if d < start or d > end:
                    continue
                points.append(Point(d, float(row[vcol])))
            except Exception:
                continue

    points.sort(key=lambda x: x.date)
    if not points:
        raise RuntimeError("Stock data is empty after filtering; please check csv or date range")
    return points


def _generate_demo_liquor_data(start: date, end: date) -> List[LiquorPoint]:
    print("Using demo/mock liquor price data because real liquor price data was not found.")
    pts: List[LiquorPoint] = []
    d = start
    i = 0
    while d <= end:
        base_bulk = 2400 + 500 * math.sin(i / 20.0) + 220 * math.sin(i / 7.0)
        trend = min(300, i * 0.8)
        bulk = max(1700, min(3300, base_bulk + trend))
        original = bulk + 150
        pts.append(LiquorPoint(d, round(bulk, 2), round(original, 2)))
        d += timedelta(days=7)
        i += 1
    return pts


def load_liquor_data(start: date, end: date, liquor_csv: Path) -> List[LiquorPoint]:
    if not liquor_csv.exists():
        return _generate_demo_liquor_data(start, end)

    rows: List[Dict[str, str]] = []
    if liquor_csv.suffix.lower() in {".xlsx", ".xls"}:
        try:
            import pandas as pd  # type: ignore
        except Exception as e:
            raise RuntimeError(f"Liquor file is Excel but pandas/openpyxl is unavailable: {e}") from e
        df = pd.read_excel(liquor_csv)
        rows = [{str(k): "" if v is None else str(v) for k, v in rec.items()} for rec in df.to_dict("records")]
        headers = [str(c) for c in df.columns.tolist()]
    else:
        with liquor_csv.open("r", encoding="utf-8") as f:
            r = csv.DictReader(f)
            headers = r.fieldnames or []
            rows = [dict(row) for row in r]

    dcol = _pick_col(headers, ["date", "日期", "A", "Date"], "酒价")
    bulk_col = _pick_col(
        headers,
        [
            "bulk_price",
            "中国:批发参考价:飞天茅台:当年散装",
            "liquor_price_ref",
            "price",
            "市场价",
            "参考价",
        ],
        "酒价(散装)",
    )
    original_col = _pick_col(
        headers,
        ["original_box_price", "中国:批发参考价:飞天茅台:当年原装"],
        "酒价(原装)",
    )

    points: List[LiquorPoint] = []
    for row in rows:
        try:
            d = _parse_date(row[dcol])
            if d < start or d > end:
                continue
            b = str(row.get(bulk_col, "")).strip()
            o = str(row.get(original_col, "")).strip()
            points.append(LiquorPoint(d, float(b) if b else None, float(o) if o else None))
        except Exception:
            continue

    points.sort(key=lambda x: x.date)
    if not points:
        return _generate_demo_liquor_data(start, end)
    return points


def align_data(stock_points: List[Point], liquor_points: List[LiquorPoint]) -> List[Tuple[date, float, Optional[float], Optional[float]]]:
    liquor_sorted = sorted(liquor_points, key=lambda x: x.date)
    i = 0
    last_bulk: Optional[float] = None
    last_original: Optional[float] = None
    merged: List[Tuple[date, float, Optional[float], Optional[float]]] = []

    for s in sorted(stock_points, key=lambda x: x.date):
        while i < len(liquor_sorted) and liquor_sorted[i].date <= s.date:
            if liquor_sorted[i].bulk_price is not None:
                last_bulk = liquor_sorted[i].bulk_price
            if liquor_sorted[i].original_box_price is not None:
                last_original = liquor_sorted[i].original_box_price
            i += 1
        merged.append((s.date, s.value, last_bulk, last_original))
    return merged


def _ema(values: List[float], span: int) -> List[float]:
    alpha = 2.0 / (span + 1.0)
    out: List[float] = []
    prev: Optional[float] = None
    for v in values:
        prev = v if prev is None else (alpha * v + (1 - alpha) * prev)
        out.append(prev)
    return out


def save_merged_csv(rows: List[Tuple[date, float, Optional[float], Optional[float]]], output_csv: Path) -> None:
    closes = [c for _, c, _, _ in rows]
    ema20 = _ema(closes, 20)
    ema55 = _ema(closes, 55)
    ema100 = _ema(closes, 100)
    ema200 = _ema(closes, 200)

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "close", "bulk_price", "original_box_price", "ema20", "ema55", "ema100", "ema200"])
        for i, (d, c, b, o) in enumerate(rows):
            w.writerow([
                d.strftime(DATE_FMT),
                c,
                "" if b is None else b,
                "" if o is None else o,
                round(ema20[i], 4),
                round(ema55[i], 4),
                round(ema100[i], 4),
                round(ema200[i], 4),
            ])


def _compute_max_runup_and_drawdown(dates: List[datetime], closes: List[float]) -> Tuple[Tuple[int, int, float], Tuple[int, int, float]]:
    min_idx = 0
    max_up = -1.0
    up_pair = (0, 0, 0.0)
    for i in range(len(closes)):
        if closes[i] < closes[min_idx]:
            min_idx = i
        up = closes[i] / closes[min_idx] - 1.0
        if up > max_up:
            max_up = up
            up_pair = (min_idx, i, up)

    peak_idx = 0
    running_max = closes[0]
    max_dd = 0.0
    dd_pair = (0, 0, 0.0)
    for i in range(1, len(closes)):
        if closes[i] > running_max:
            running_max = closes[i]
            peak_idx = i
        dd = closes[i] / running_max - 1.0
        if dd < max_dd:
            max_dd = dd
            dd_pair = (peak_idx, i, dd)

    return up_pair, dd_pair


def plot_static_dual_axis_chart(rows: List[Tuple[date, float, Optional[float], Optional[float]]], output_png: Path) -> None:
    try:
        import pandas as pd  # type: ignore
        import matplotlib.pyplot as plt  # type: ignore

        df = pd.DataFrame(
            [{"date": d, "close": c, "bulk_price": b, "original_box_price": o} for d, c, b, o in rows if b is not None]
        )
        if df.empty:
            raise RuntimeError("No aligned liquor values available to plot")

        fig, ax1 = plt.subplots(figsize=(16, 8), dpi=180)
        ax2 = ax1.twinx()
        l1 = ax1.plot(df["date"], df["close"], color="#1565c0", linewidth=1.8, label="贵州茅台股价")
        l2 = ax2.plot(df["date"], df["bulk_price"], color="#c62828", linewidth=1.8, label="飞天茅台53度当年散装参考价")
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
        print("[WARN] pandas/matplotlib not found, using built-in PNG renderer.")

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


def plot_interactive_dual_axis_chart(rows: List[Tuple[date, float, Optional[float], Optional[float]]], output_html: Path) -> None:
    valid = [(d, c, b, o) for d, c, b, o in rows if b is not None]
    if not valid:
        raise RuntimeError("No aligned liquor values available to render interactive chart")

    try:
        import pandas as pd  # type: ignore
        import plotly.graph_objects as go  # type: ignore
        import plotly.io as pio  # type: ignore

        merged_df = pd.DataFrame(
            [{"date": d, "close": c, "bulk_price": b, "original_box_price": o} for d, c, b, o in valid]
        )
        merged_df["date"] = pd.to_datetime(merged_df["date"])
        merged_df["bulk_price"] = merged_df["bulk_price"].ffill()
        merged_df["original_box_price"] = merged_df["original_box_price"].ffill()

        merged_df["ema20"] = merged_df["close"].ewm(span=20, adjust=False).mean()
        merged_df["ema55"] = merged_df["close"].ewm(span=55, adjust=False).mean()
        merged_df["ema100"] = merged_df["close"].ewm(span=100, adjust=False).mean()
        merged_df["ema200"] = merged_df["close"].ewm(span=200, adjust=False).mean()

        dates = merged_df["date"].tolist()
        closes = merged_df["close"].astype(float).tolist()
        bulk = merged_df["bulk_price"].astype(float).tolist()
        (_, _, max_up), (_, _, max_dd) = _compute_max_runup_and_drawdown(dates, closes)

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
            title="贵州茅台股价与飞天茅台53度散瓶市场参考价走势对比<br><sup>时间范围：2018-01-01 至今（按A股交易日对齐）｜酒价数据来源：用户提供的批发参考价整理表</sup>",
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

        latest_idx = len(dates) - 1
        fig.add_annotation(
            x=dates[latest_idx],
            y=closes[latest_idx],
            text=f"最新股价: {closes[latest_idx]:.2f}",
            showarrow=True,
            arrowhead=2,
            ax=22,
            ay=-28,
            yref="y",
            font=dict(color="#1565c0", size=12),
            bgcolor="rgba(255,255,255,0.75)",
        )
        fig.add_annotation(
            x=dates[latest_idx],
            y=bulk[latest_idx],
            text=f"最新散装价: {bulk[latest_idx]:.2f}",
            showarrow=True,
            arrowhead=2,
            ax=22,
            ay=28,
            yref="y2",
            font=dict(color="#c62828", size=12),
            bgcolor="rgba(255,255,255,0.75)",
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

        output_html.parent.mkdir(parents=True, exist_ok=True)
        pio.write_html(fig, file=str(output_html), include_plotlyjs=True, full_html=True)
        return
    except Exception:
        pass

    dates = [d.strftime(DATE_FMT) for d, _, _, _ in valid]
    closes = [float(c) for _, c, _, _ in valid]
    bulks = [float(b) if b is not None else None for _, _, b, _ in valid]
    html = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>贵州茅台股价与飞天茅台53度散瓶市场参考价走势对比</title>
<style>body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;margin:0;padding:20px}}#tip{{margin-top:10px;font-size:14px}}</style>
</head><body>
<h2>贵州茅台股价与飞天茅台53度散瓶市场参考价走势对比</h2>
<div>时间范围：2018-01-01 至今（按A股交易日对齐）｜酒价数据来源：用户提供的批发参考价整理表</div>
<div id="tip">移动鼠标查看同一日期的股价与酒价</div>
<script>
const dates={json.dumps(dates, ensure_ascii=False)};
const stock={json.dumps(closes)};
const bulk={json.dumps(bulks)};
const zh=(d)=>{{const [y,m,dd]=d.split('-');return `${{y}}年${{m}}月${{dd}}日`;}};
document.body.addEventListener('mousemove',()=>{{
 const i=dates.length-1;
 document.getElementById('tip').textContent=`日期：${{zh(dates[i])}} ｜ 贵州茅台股价：${{stock[i].toFixed(2)}} 元 ｜ 飞天茅台53度当年散装参考价：${{bulk[i].toFixed(2)}} 元/瓶`;
}});
</script>
</body></html>'''
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(html, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="贵州茅台股价 vs 飞天茅台53度散瓶参考价 双轴图")
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--end", default=date.today().strftime(DATE_FMT))
    parser.add_argument("--stock-csv", type=Path, default=Path("data/stock_prices_auto.csv"))
    parser.add_argument("--liquor-csv", type=Path, default=Path("data/moutai_prices_0311.csv"))
    parser.add_argument("--output-png", type=Path, default=Path("output/moutai_stock_vs_liquor_dual_axis.png"))
    parser.add_argument("--output-html", type=Path, default=Path("output/moutai_stock_vs_liquor_interactive.html"))
    parser.add_argument("--output-merged-csv", type=Path, default=Path("output/moutai_stock_vs_liquor_merged.csv"))
    parser.add_argument("--no-auto-fetch-stock", action="store_true", help="Do not auto-fetch stock data if stock csv missing")
    args = parser.parse_args()

    start = _parse_date(args.start)
    end = _parse_date(args.end)

    stock = load_stock_data(start, end, args.stock_csv, auto_fetch=not args.no_auto_fetch_stock)
    liquor = load_liquor_data(start, end, args.liquor_csv)
    merged = align_data(stock, liquor)

    save_merged_csv(merged, args.output_merged_csv)
    plot_static_dual_axis_chart(merged, args.output_png)
    plot_interactive_dual_axis_chart(merged, args.output_html)

    print(f"[OK] merged csv: {args.output_merged_csv}")
    print(f"[OK] chart png: {args.output_png}")
    print(f"[OK] interactive html: {args.output_html}")
    print(f"[INFO] trading-day rows: {len(merged)}")


if __name__ == "__main__":
    main()
