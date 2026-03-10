from __future__ import annotations

import argparse
import csv
import json
import re
import struct
import zlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DATE_FMT = "%Y-%m-%d"


@dataclass
class Record:
    date: datetime
    value: Optional[float]


def http_get(url: str, timeout: int = 20) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def fetch_stock_prices(start: str, end: str, output_csv: Path, raw_csv: Path) -> None:
    params = {
        "secid": "1.600519",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "klt": "101",
        "fqt": "1",
        "beg": start.replace("-", ""),
        "end": end.replace("-", ""),
    }
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get?" + urlencode(params)
    try:
        payload = json.loads(http_get(url))
    except Exception as e:
        raise RuntimeError(f"A股抓取失败（Eastmoney接口不可用）: {e}") from e

    klines = (((payload or {}).get("data") or {}).get("klines")) or []
    if not klines:
        raise RuntimeError("A股抓取失败：接口返回为空，可能是日期范围无数据或被限流")

    raw_csv.parent.mkdir(parents=True, exist_ok=True)
    with raw_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "open", "close", "high", "low", "volume", "turnover", "amp", "pct_change", "change", "turnover_rate"])
        for row in klines:
            p = row.split(",")
            w.writerow(p)

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "close"])
        for row in klines:
            p = row.split(",")
            w.writerow([p[0], p[2]])


def extract_liquor_records_from_html(html: str, keyword_include: List[str], keyword_exclude: List[str]) -> List[Tuple[str, str, float, str]]:
    records: List[Tuple[str, str, float, str]] = []
    pattern = re.compile(
        r"(?P<title>[\u4e00-\u9fa5A-Za-z0-9（）()·\-\s]{6,80})[^\n]{0,200}?"
        r"(?P<date>20\d{2}[-/]\d{1,2}[-/]\d{1,2})[^\n]{0,120}?"
        r"(?:¥|￥|RMB)?\s*(?P<price>\d{3,6}(?:\.\d{1,2})?)"
    )
    for m in pattern.finditer(html):
        title = re.sub(r"\s+", "", m.group("title"))
        if not all(k in title for k in keyword_include):
            continue
        if any(k in title for k in keyword_exclude):
            continue
        d = m.group("date").replace("/", "-")
        try:
            dt = datetime.strptime(d, DATE_FMT).strftime(DATE_FMT)
            price = float(m.group("price"))
        except ValueError:
            continue
        snippet = html[max(0, m.start() - 40): m.end() + 40].replace("\n", " ")
        records.append((dt, title, price, snippet))
    return records


def fetch_liquor_prices(
    start: str,
    end: str,
    output_csv: Path,
    raw_csv: Path,
    source_url: str,
    product_name: str,
    caliber: str,
) -> None:
    """Auto-fetch liquor secondary market daily quotes by scraping source page(s)."""
    include = ["茅台", "26", "飞天"]
    exclude = ["生肖", "礼盒", "年份酒", "整箱"]

    try:
        html = http_get(source_url)
    except Exception as e:
        raise RuntimeError(f"酒价抓取失败（无法访问来源页面）: {e}") from e

    rows = extract_liquor_records_from_html(html, include, exclude)
    if not rows:
        raise RuntimeError(
            "酒价抓取失败：未匹配到有效记录。可能是页面动态加载/反爬、DOM结构变更，或关键词规则不匹配。"
        )

    start_d = datetime.strptime(start, DATE_FMT)
    end_d = datetime.strptime(end, DATE_FMT)
    rows = [r for r in rows if start_d <= datetime.strptime(r[0], DATE_FMT) <= end_d]
    if not rows:
        raise RuntimeError("酒价抓取失败：指定日期区间内无可用报价")

    raw_csv.parent.mkdir(parents=True, exist_ok=True)
    with raw_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "title", "price", "source", "caliber", "snippet"])
        for d, title, price, snip in rows:
            w.writerow([d, title, price, source_url, caliber, snip])

    day_prices: Dict[str, List[float]] = {}
    for d, _, p, _ in rows:
        day_prices.setdefault(d, []).append(p)

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "secondary_price", "source", "product", "caliber", "aggregation", "quote_count"])
        for d in sorted(day_prices):
            prices = sorted(day_prices[d])
            w.writerow([d, median(prices), source_url, product_name, caliber, "median", len(prices)])


def load_series(csv_path: Path, value_col: str) -> List[Record]:
    out: List[Record] = []
    with csv_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            d = datetime.strptime(row["date"], DATE_FMT)
            raw = row.get(value_col, "")
            out.append(Record(d, float(raw) if raw not in ("", None) else None))
    return sorted(out, key=lambda x: x.date)


def clean_series(records: List[Record], name: str) -> List[Record]:
    dedup: Dict[datetime, List[float]] = {}
    for r in records:
        if r.value is None:
            continue
        dedup.setdefault(r.date, []).append(r.value)
    cleaned = [Record(d, median(vs)) for d, vs in dedup.items()]
    cleaned.sort(key=lambda x: x.date)

    vals = [r.value for r in cleaned if r.value is not None]
    if len(vals) >= 4:
        sv = sorted(vals)
        q1 = sv[len(sv) // 4]
        q3 = sv[(len(sv) * 3) // 4]
        iqr = q3 - q1
        lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        outliers = [r for r in cleaned if r.value is not None and (r.value < lo or r.value > hi)]
        if outliers:
            print(f"[WARN] {name} 检测到异常值 {len(outliers)} 条（IQR法）")
            for r in outliers[:5]:
                print(f"[WARN]   {r.date.strftime(DATE_FMT)} -> {r.value}")
    return cleaned


def align_series(liquor: List[Record], stock: List[Record], align_mode: str, fill_mode: str) -> List[Tuple[datetime, Optional[float], Optional[float]]]:
    lmap = {r.date: r.value for r in liquor}
    smap = {r.date: r.value for r in stock}

    if align_mode == "inner":
        dates = sorted(set(lmap) & set(smap))
    else:
        dates = sorted(set(lmap))

    merged = [(d, lmap.get(d), smap.get(d)) for d in dates]
    if fill_mode == "ffill":
        last_l = None
        last_s = None
        fixed = []
        for d, lv, sv in merged:
            if lv is not None:
                last_l = lv
            if sv is not None:
                last_s = sv
            fixed.append((d, lv if lv is not None else last_l, sv if sv is not None else last_s))
        merged = fixed
    return merged


def normalize(merged: List[Tuple[datetime, Optional[float], Optional[float]]]) -> List[Tuple[datetime, float, float]]:
    valid = [(d, l, s) for d, l, s in merged if l is not None and s is not None]
    if not valid:
        raise ValueError("对齐后无有效数据")
    l0, s0 = valid[0][1], valid[0][2]
    return [(d, l / l0 * 100, s / s0 * 100) for d, l, s in valid]


def _scale(v: float, lo: float, hi: float, y0: float, y1: float) -> float:
    if hi == lo:
        return (y0 + y1) / 2
    return y1 - (v - lo) / (hi - lo) * (y1 - y0)


def render_svg(data: List[Tuple[datetime, float, float]], output: Path, title: str, y1_name: str, y2_name: str) -> None:
    width, height = 1200, 680
    ml, mr, mt, mb = 90, 90, 70, 90
    pw, ph = width - ml - mr, height - mt - mb
    n = len(data)
    xstep = pw / (n - 1) if n > 1 else 0

    v1 = [x[1] for x in data]
    v2 = [x[2] for x in data]
    m1, M1 = min(v1), max(v1)
    m2, M2 = min(v2), max(v2)

    p1, p2 = [], []
    labels = []
    for i, (d, a, b) in enumerate(data):
        x = ml + i * xstep
        p1.append((x, _scale(a, m1, M1, mt, mt + ph)))
        p2.append((x, _scale(b, m2, M2, mt, mt + ph)))
        labels.append((x, d.strftime("%m-%d")))

    def poly(ps: List[Tuple[float, float]]) -> str:
        return " ".join(f"{x:.2f},{y:.2f}" for x, y in ps)

    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">']
    svg += ['<rect width="100%" height="100%" fill="white"/>', f'<text x="600" y="35" text-anchor="middle" font-size="24">{title}</text>']
    svg += [f'<line x1="{ml}" y1="{mt}" x2="{ml}" y2="{mt+ph}" stroke="#333"/>', f'<line x1="{ml+pw}" y1="{mt}" x2="{ml+pw}" y2="{mt+ph}" stroke="#333"/>', f'<line x1="{ml}" y1="{mt+ph}" x2="{ml+pw}" y2="{mt+ph}" stroke="#333"/>']
    every = max(1, n // 8)
    for i, (x, lab) in enumerate(labels):
        if i % every == 0 or i == n - 1:
            svg.append(f'<text x="{x:.2f}" y="{mt+ph+25}" text-anchor="middle" font-size="12">{lab}</text>')
    svg += [f'<polyline fill="none" stroke="#c62828" stroke-width="2.5" points="{poly(p1)}"/>', f'<polyline fill="none" stroke="#1565c0" stroke-width="2.5" points="{poly(p2)}"/>']
    svg += [f'<text x="20" y="330" transform="rotate(-90 20,330)" font-size="12" fill="#c62828">{y1_name}</text>', f'<text x="1180" y="330" transform="rotate(90 1180,330)" font-size="12" fill="#1565c0">{y2_name}</text>', '</svg>']
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(svg), encoding="utf-8")


def write_png(path: Path, width: int, height: int, pixels: bytes) -> None:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack("!I", len(data)) + tag + data + struct.pack("!I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + pixels[y * width * 3:(y + 1) * width * 3] for y in range(height))
    data = zlib.compress(raw, 9)
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack("!IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", data)
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


def render_png(data: List[Tuple[datetime, float, float]], output: Path) -> None:
    w, h = 1200, 680
    ml, mr, mt, mb = 90, 90, 70, 90
    pw, ph = w - ml - mr, h - mt - mb
    pix = bytearray([255] * (w * h * 3))

    def set_px(x: int, y: int, c: Tuple[int, int, int]) -> None:
        if 0 <= x < w and 0 <= y < h:
            i = (y * w + x) * 3
            pix[i:i+3] = bytes(c)

    def line(x0: int, y0: int, x1: int, y1: int, c: Tuple[int, int, int]) -> None:
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        while True:
            set_px(x0, y0, c)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    v1 = [x[1] for x in data]
    v2 = [x[2] for x in data]
    m1, M1 = min(v1), max(v1)
    m2, M2 = min(v2), max(v2)
    n = len(data)
    xstep = pw / (n - 1) if n > 1 else 0
    p1, p2 = [], []
    for i, (_, a, b) in enumerate(data):
        x = int(ml + i * xstep)
        p1.append((x, int(_scale(a, m1, M1, mt, mt + ph))))
        p2.append((x, int(_scale(b, m2, M2, mt, mt + ph))))

    line(ml, mt, ml, mt + ph, (40, 40, 40))
    line(ml + pw, mt, ml + pw, mt + ph, (40, 40, 40))
    line(ml, mt + ph, ml + pw, mt + ph, (40, 40, 40))
    for i in range(1, len(p1)):
        line(*p1[i-1], *p1[i], (198, 40, 40))
        line(*p2[i-1], *p2[i], (21, 101, 192))

    output.parent.mkdir(parents=True, exist_ok=True)
    write_png(output, w, h, bytes(pix))


def save_aligned_csv(path: Path, merged: List[Tuple[datetime, Optional[float], Optional[float]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "secondary_price", "stock_close"])
        for d, l, s in merged:
            w.writerow([d.strftime(DATE_FMT), l if l is not None else "", s if s is not None else ""])


def main() -> None:
    p = argparse.ArgumentParser(description="全自动抓取贵州茅台酒价与A股并出图")
    p.add_argument("--fetch-stock", action="store_true")
    p.add_argument("--fetch-liquor", action="store_true")
    p.add_argument("--start", default="2025-01-01")
    p.add_argument("--end", default=datetime.now().strftime(DATE_FMT))
    p.add_argument("--stock-csv", type=Path, default=Path("data/stock_prices_auto.csv"))
    p.add_argument("--stock-raw-csv", type=Path, default=Path("data/stock_prices_auto_raw.csv"))
    p.add_argument("--liquor-csv", type=Path, default=Path("data/liquor_prices_auto.csv"))
    p.add_argument("--liquor-raw-csv", type=Path, default=Path("data/liquor_prices_auto_raw.csv"))
    p.add_argument("--liquor-source-url", default="https://www.jiuxiwang.cn/")
    p.add_argument("--product-name", default="贵州茅台酒（26年飞天茅台）")
    p.add_argument("--price-caliber", default="散瓶/单瓶口径")
    p.add_argument("--align-mode", choices=["inner", "left"], default="inner")
    p.add_argument("--fill-mode", choices=["none", "ffill"], default="none")
    p.add_argument("--output-prefix", default="output/moutai_auto")
    args = p.parse_args()

    if args.fetch_stock:
        fetch_stock_prices(args.start, args.end, args.stock_csv, args.stock_raw_csv)
        print(f"[OK] 已抓取A股数据 -> {args.stock_csv}")
    if args.fetch_liquor:
        fetch_liquor_prices(args.start, args.end, args.liquor_csv, args.liquor_raw_csv, args.liquor_source_url, args.product_name, args.price_caliber)
        print(f"[OK] 已抓取酒价数据 -> {args.liquor_csv}")

    if not args.stock_csv.exists():
        raise FileNotFoundError(f"缺少股票CSV: {args.stock_csv}，请先 --fetch-stock")
    if not args.liquor_csv.exists():
        raise FileNotFoundError(f"缺少酒价CSV: {args.liquor_csv}，请先 --fetch-liquor")

    liquor = clean_series(load_series(args.liquor_csv, "secondary_price"), "酒价")
    stock = clean_series(load_series(args.stock_csv, "close"), "A股")
    merged = align_series(liquor, stock, args.align_mode, args.fill_mode)
    if args.fill_mode == "none":
        merged = [(d, l, s) for d, l, s in merged if l is not None and s is not None]
    if not merged:
        raise ValueError("对齐后无数据，请检查对齐参数或抓取结果")

    aligned_csv = Path(args.output_prefix + "_aligned.csv")
    save_aligned_csv(aligned_csv, merged)

    dual_data = [(d, float(l), float(s)) for d, l, s in merged if l is not None and s is not None]
    norm_data = normalize(merged)

    render_svg(dual_data, Path(args.output_prefix + "_dual.svg"), "贵州茅台酒价 vs A股（双轴）", "酒价(元)", "A股收盘(元)")
    render_png(dual_data, Path(args.output_prefix + "_dual.png"))
    render_svg(norm_data, Path(args.output_prefix + "_normalized.svg"), "贵州茅台酒价 vs A股（归一化：首日=100）", "酒价指数", "股价指数")
    render_png(norm_data, Path(args.output_prefix + "_normalized.png"))
    print("[OK] 已输出双轴与归一化图（SVG + PNG）")


if __name__ == "__main__":
    main()
