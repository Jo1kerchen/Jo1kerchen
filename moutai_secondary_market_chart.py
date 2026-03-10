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
    """Load 贵州茅台收盘价（主时间轴=交易日）。"""
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
                v = float(row[vcol])
                points.append(Point(d, v))
            except Exception:
                continue

    points.sort(key=lambda x: x.date)
    if not points:
        raise RuntimeError("Stock data is empty after filtering; please check csv or date range")
    return points


def _generate_demo_liquor_data(start: date, end: date) -> List[Point]:
    print("Using demo/mock liquor price data because real liquor price data was not found.")
    pts: List[Point] = []
    d = start
    i = 0
    # weekly demo points, smooth trend roughly 1700~3300
    while d <= end:
        base = 2400 + 500 * math.sin(i / 20.0) + 220 * math.sin(i / 7.0)
        trend = min(300, i * 0.8)
        v = max(1700, min(3300, base + trend))
        pts.append(Point(d, round(v, 2)))
        d += timedelta(days=7)
        i += 1
    return pts


def load_liquor_data(start: date, end: date, liquor_csv: Path) -> List[Point]:
    """Load 飞天茅台53度散瓶市场参考价。支持多字段映射；无文件则自动mock。"""
    if not liquor_csv.exists():
        return _generate_demo_liquor_data(start, end)

    points: List[Point] = []
    with liquor_csv.open("r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        headers = r.fieldnames or []
        dcol = _pick_col(headers, ["date", "日期"], "酒价")
        vcol = _pick_col(headers, ["liquor_price_ref", "price", "市场价", "参考价", "secondary_price"], "酒价")
        for row in r:
            try:
                d = _parse_date(row[dcol])
                if d < start or d > end:
                    continue
                v = float(row[vcol])
                points.append(Point(d, v))
            except Exception:
                continue

    points.sort(key=lambda x: x.date)
    if not points:
        return _generate_demo_liquor_data(start, end)
    return points


def align_data(stock_points: List[Point], liquor_points: List[Point]) -> List[Tuple[date, float, Optional[float]]]:
    """以A股交易日为主轴，对酒价做向前填充映射。"""
    liquor_sorted = sorted(liquor_points, key=lambda x: x.date)
    i = 0
    last_liquor: Optional[float] = None
    merged: List[Tuple[date, float, Optional[float]]] = []

    for s in sorted(stock_points, key=lambda x: x.date):
        while i < len(liquor_sorted) and liquor_sorted[i].date <= s.date:
            last_liquor = liquor_sorted[i].value
            i += 1
        merged.append((s.date, s.value, last_liquor))

    return merged


def save_merged_csv(rows: List[Tuple[date, float, Optional[float]]], output_csv: Path) -> None:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "close", "liquor_price_ref"])
        for d, c, l in rows:
            w.writerow([d.strftime(DATE_FMT), c, "" if l is None else l])


def _render_with_matplotlib(rows: List[Tuple[date, float, Optional[float]]], output_png: Path) -> bool:
    try:
        import pandas as pd  # type: ignore
        import matplotlib.pyplot as plt  # type: ignore
    except Exception:
        return False

    df = pd.DataFrame([
        {"date": d, "close": c, "liquor_price_ref": l} for d, c, l in rows if l is not None
    ])
    if df.empty:
        raise RuntimeError("No aligned liquor values available to plot")

    fig, ax1 = plt.subplots(figsize=(16, 8), dpi=180)
    ax2 = ax1.twinx()

    l1 = ax1.plot(df["date"], df["close"], color="#1565c0", linewidth=1.8, label="贵州茅台收盘价")
    l2 = ax2.plot(df["date"], df["liquor_price_ref"], color="#c62828", linewidth=1.8, label="飞天茅台53度散瓶参考价")

    ax1.set_title("贵州茅台股价与飞天茅台53度散瓶市场参考价走势对比\n时间范围：2018-01-01 至今（按A股交易日对齐）")
    ax1.set_xlabel("日期")
    ax1.set_ylabel("贵州茅台收盘价（元）", color="#1565c0")
    ax2.set_ylabel("飞天茅台53度散瓶参考价（元/瓶）", color="#c62828")
    ax1.grid(True, linestyle="--", alpha=0.25)

    lines = l1 + l2
    labels = [x.get_label() for x in lines]
    ax1.legend(lines, labels, loc="upper left")
    fig.autofmt_xdate()
    fig.tight_layout()

    output_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_png)
    plt.close(fig)
    return True


def _write_png(path: Path, width: int, height: int, pixels: bytes) -> None:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack("!I", len(data)) + tag + data + struct.pack("!I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + pixels[y * width * 3:(y + 1) * width * 3] for y in range(height))
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack("!IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


def _draw_line(img: bytearray, w: int, h: int, x0: int, y0: int, x1: int, y1: int, c: Tuple[int, int, int]) -> None:
    def set_px(x: int, y: int) -> None:
        if 0 <= x < w and 0 <= y < h:
            i = (y * w + x) * 3
            img[i:i + 3] = bytes(c)

    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        set_px(x0, y0)
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def _scale(v: float, lo: float, hi: float, y0: float, y1: float) -> float:
    if hi == lo:
        return (y0 + y1) / 2
    return y1 - (v - lo) / (hi - lo) * (y1 - y0)


def _render_simple_png(rows: List[Tuple[date, float, Optional[float]]], output_png: Path) -> None:
    valid = [(d, c, l) for d, c, l in rows if l is not None]
    if not valid:
        raise RuntimeError("No aligned liquor values available to plot")

    w, h = 1800, 900
    ml, mr, mt, mb = 120, 120, 80, 100
    pw, ph = w - ml - mr, h - mt - mb
    img = bytearray([255] * (w * h * 3))

    cvals = [x[1] for x in valid]
    lvals = [x[2] for x in valid]
    cmin, cmax = min(cvals), max(cvals)
    lmin, lmax = min(lvals), max(lvals)
    n = len(valid)
    step = pw / (n - 1) if n > 1 else 0

    stock_pts, liquor_pts = [], []
    for i, (_, c, l) in enumerate(valid):
        x = int(ml + i * step)
        stock_pts.append((x, int(_scale(c, cmin, cmax, mt, mt + ph))))
        liquor_pts.append((x, int(_scale(l, lmin, lmax, mt, mt + ph))))

    # axes
    _draw_line(img, w, h, ml, mt, ml, mt + ph, (40, 40, 40))
    _draw_line(img, w, h, ml + pw, mt, ml + pw, mt + ph, (40, 40, 40))
    _draw_line(img, w, h, ml, mt + ph, ml + pw, mt + ph, (40, 40, 40))

    for i in range(1, len(stock_pts)):
        _draw_line(img, w, h, *stock_pts[i - 1], *stock_pts[i], (21, 101, 192))
        _draw_line(img, w, h, *liquor_pts[i - 1], *liquor_pts[i], (198, 40, 40))

    output_png.parent.mkdir(parents=True, exist_ok=True)
    _write_png(output_png, w, h, bytes(img))


def plot_dual_axis_chart(rows: List[Tuple[date, float, Optional[float]]], output_png: Path) -> None:
    """Render dual-axis chart as PNG. Prefer matplotlib; fallback to built-in renderer."""
    if _render_with_matplotlib(rows, output_png):
        return
    print("[WARN] pandas/matplotlib not found, using built-in PNG renderer.")
    _render_simple_png(rows, output_png)


def main() -> None:
    parser = argparse.ArgumentParser(description="贵州茅台股价 vs 飞天茅台53度散瓶参考价 双轴图")
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--end", default=date.today().strftime(DATE_FMT))
    parser.add_argument("--stock-csv", type=Path, default=Path("data/stock_prices_auto.csv"))
    parser.add_argument("--liquor-csv", type=Path, default=Path("data/moutai_liquor_prices.csv"))
    parser.add_argument("--output-png", type=Path, default=Path("output/moutai_stock_vs_liquor_dual_axis.png"))
    parser.add_argument("--output-merged-csv", type=Path, default=Path("output/moutai_stock_vs_liquor_merged.csv"))
    parser.add_argument("--no-auto-fetch-stock", action="store_true", help="Do not auto-fetch stock data if stock csv missing")
    args = parser.parse_args()

    start = _parse_date(args.start)
    end = _parse_date(args.end)

    stock = load_stock_data(start, end, args.stock_csv, auto_fetch=not args.no_auto_fetch_stock)
    liquor = load_liquor_data(start, end, args.liquor_csv)
    merged = align_data(stock, liquor)

    save_merged_csv(merged, args.output_merged_csv)
    plot_dual_axis_chart(merged, args.output_png)

    print(f"[OK] merged csv: {args.output_merged_csv}")
    print(f"[OK] chart png: {args.output_png}")
    print(f"[INFO] trading-day rows: {len(merged)}")


if __name__ == "__main__":
    main()
