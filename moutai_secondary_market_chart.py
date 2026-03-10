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
from typing import List, Optional, Tuple
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


def _generate_demo_liquor_data(start: date, end: date) -> List[Point]:
    print("Using demo/mock liquor price data because real liquor price data was not found.")
    pts: List[Point] = []
    d = start
    i = 0
    while d <= end:
        base = 2400 + 500 * math.sin(i / 20.0) + 220 * math.sin(i / 7.0)
        trend = min(300, i * 0.8)
        v = max(1700, min(3300, base + trend))
        pts.append(Point(d, round(v, 2)))
        d += timedelta(days=7)
        i += 1
    return pts


def load_liquor_data(start: date, end: date, liquor_csv: Path) -> List[Point]:
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
                points.append(Point(d, float(row[vcol])))
            except Exception:
                continue

    points.sort(key=lambda x: x.date)
    if not points:
        return _generate_demo_liquor_data(start, end)
    return points


def align_data(stock_points: List[Point], liquor_points: List[Point]) -> List[Tuple[date, float, Optional[float]]]:
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


def plot_static_dual_axis_chart(rows: List[Tuple[date, float, Optional[float]]], output_png: Path) -> None:
    # try matplotlib first
    try:
        import pandas as pd  # type: ignore
        import matplotlib.pyplot as plt  # type: ignore

        df = pd.DataFrame([{"date": d, "close": c, "liquor_price_ref": l} for d, c, l in rows if l is not None])
        if df.empty:
            raise RuntimeError("No aligned liquor values available to plot")

        fig, ax1 = plt.subplots(figsize=(16, 8), dpi=180)
        ax2 = ax1.twinx()
        l1 = ax1.plot(df["date"], df["close"], color="#1565c0", linewidth=1.8, label="贵州茅台股价")
        l2 = ax2.plot(df["date"], df["liquor_price_ref"], color="#c62828", linewidth=1.8, label="飞天茅台53度散瓶参考价")
        ax1.set_title("贵州茅台股价与飞天茅台53度散瓶市场参考价走势对比\n时间范围：2018-01-01 至今（按A股交易日对齐）")
        ax1.set_xlabel("日期")
        ax1.set_ylabel("贵州茅台收盘价（元）", color="#1565c0")
        ax2.set_ylabel("飞天茅台53度散瓶参考价（元/瓶）", color="#c62828")
        ax1.grid(True, linestyle="--", alpha=0.25)
        lines = l1 + l2
        ax1.legend(lines, [x.get_label() for x in lines], loc="upper left")
        fig.autofmt_xdate()
        fig.tight_layout()
        output_png.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_png)
        plt.close(fig)
        return
    except Exception:
        print("[WARN] pandas/matplotlib not found, using built-in PNG renderer.")

    # fallback builtin png line chart
    valid = [(d, c, l) for d, c, l in rows if l is not None]
    if not valid:
        raise RuntimeError("No aligned liquor values available to plot")
    w, h = 1800, 900
    ml, mr, mt, mb = 120, 120, 80, 100
    pw, ph = w - ml - mr, h - mt - mb
    img = bytearray([255] * (w * h * 3))

    def set_px(x: int, y: int, c: Tuple[int, int, int]) -> None:
        if 0 <= x < w and 0 <= y < h:
            i = (y * w + x) * 3
            img[i:i + 3] = bytes(c)

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

    def scale(v: float, lo: float, hi: float, y0: float, y1: float) -> float:
        if hi == lo:
            return (y0 + y1) / 2
        return y1 - (v - lo) / (hi - lo) * (y1 - y0)

    cvals = [x[1] for x in valid]
    lvals = [x[2] for x in valid]
    cmin, cmax = min(cvals), max(cvals)
    lmin, lmax = min(lvals), max(lvals)
    step = pw / (len(valid) - 1) if len(valid) > 1 else 0
    sp, lp = [], []
    for i, (_, c, l) in enumerate(valid):
        x = int(ml + i * step)
        sp.append((x, int(scale(c, cmin, cmax, mt, mt + ph))))
        lp.append((x, int(scale(l, lmin, lmax, mt, mt + ph))))

    line(ml, mt, ml, mt + ph, (40, 40, 40))
    line(ml + pw, mt, ml + pw, mt + ph, (40, 40, 40))
    line(ml, mt + ph, ml + pw, mt + ph, (40, 40, 40))
    for i in range(1, len(sp)):
        line(*sp[i - 1], *sp[i], (21, 101, 192))
        line(*lp[i - 1], *lp[i], (198, 40, 40))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack("!I", len(data)) + tag + data + struct.pack("!I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + bytes(img[y * w * 3:(y + 1) * w * 3]) for y in range(h))
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack("!IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    output_png.parent.mkdir(parents=True, exist_ok=True)
    output_png.write_bytes(png)


def plot_interactive_dual_axis_chart(rows: List[Tuple[date, float, Optional[float]]], output_html: Path) -> None:
    """Generate shareable interactive html. Prefer Plotly; fallback to vanilla JS interactive SVG."""
    valid = [(d, c, l) for d, c, l in rows if l is not None]
    if not valid:
        raise RuntimeError("No aligned liquor values available to render interactive chart")

    # Try plotly first (preferred)
    try:
        import plotly.graph_objects as go  # type: ignore
        import plotly.io as pio  # type: ignore

        dates = [d.strftime(DATE_FMT) for d, _, _ in valid]
        closes = [c for _, c, _ in valid]
        liquors = [l for _, _, l in valid]

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=dates, y=closes, name="贵州茅台股价", mode="lines", line=dict(color="#1565c0"), yaxis="y1"))
        fig.add_trace(go.Scatter(x=dates, y=liquors, name="飞天茅台53度散瓶参考价", mode="lines", line=dict(color="#c62828"), yaxis="y2"))

        fig.update_layout(
            title="贵州茅台股价与飞天茅台53度散瓶市场参考价走势对比<br><sup>时间范围：2018-01-01 至今（按A股交易日对齐）</sup>",
            xaxis=dict(title="日期"),
            yaxis=dict(title="贵州茅台收盘价（元）", side="left"),
            yaxis2=dict(title="飞天茅台53度散瓶参考价（元/瓶）", overlaying="y", side="right"),
            hovermode="x unified",
            template="plotly_white",
        )

        latest_date = dates[-1]
        fig.add_annotation(x=latest_date, y=closes[-1], text=f"最新股价: {closes[-1]:.2f}", showarrow=True, arrowhead=2, yref="y")
        fig.add_annotation(x=latest_date, y=liquors[-1], text=f"最新酒价: {liquors[-1]:.2f}", showarrow=True, arrowhead=2, yref="y2")

        output_html.parent.mkdir(parents=True, exist_ok=True)
        pio.write_html(fig, file=str(output_html), include_plotlyjs=True, full_html=True)
        return
    except Exception:
        pass

    # Fallback interactive HTML (no external dependency)
    dates = [d.strftime(DATE_FMT) for d, _, _ in valid]
    closes = [float(c) for _, c, _ in valid]
    liquors = [float(l) for _, _, l in valid]

    html = f'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>贵州茅台股价与飞天茅台53度散瓶市场参考价走势对比</title>
<style>
body {{ font-family: -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; margin: 0; padding: 20px; }}
#wrap {{ max-width: 1280px; margin: 0 auto; }}
#title {{ font-size: 22px; font-weight: 600; }}
#subtitle {{ color: #666; margin: 6px 0 12px; }}
#chart {{ border: 1px solid #ddd; }}
#tip {{ margin-top: 10px; font-size: 14px; }}
.legend {{ margin-top: 8px; font-size: 14px; }}
.legend span {{ margin-right: 16px; }}
</style>
</head>
<body>
<div id="wrap">
  <div id="title">贵州茅台股价与飞天茅台53度散瓶市场参考价走势对比</div>
  <div id="subtitle">时间范围：2018-01-01 至今（按A股交易日对齐）</div>
  <svg id="chart" width="1240" height="620"></svg>
  <div class="legend"><span style="color:#1565c0;">■ 贵州茅台股价</span><span style="color:#c62828;">■ 飞天茅台53度散瓶参考价</span></div>
  <div id="tip">移动鼠标查看同一日期的股价与酒价</div>
</div>
<script>
const dates = {json.dumps(dates, ensure_ascii=False)};
const stock = {json.dumps(closes)};
const liquor = {json.dumps(liquors)};
const svg = document.getElementById('chart');
const NS='http://www.w3.org/2000/svg';
const W=1240,H=620,ml=80,mr=90,mt=50,mb=70,pw=W-ml-mr,ph=H-mt-mb;
const sMin=Math.min(...stock), sMax=Math.max(...stock);
const lMin=Math.min(...liquor), lMax=Math.max(...liquor);
const x=(i)=>ml+(pw*(i/(dates.length-1||1)));
const yS=(v)=>mt+ph-(v-sMin)/(sMax-sMin||1)*ph;
const yL=(v)=>mt+ph-(v-lMin)/(lMax-lMin||1)*ph;
function add(tag,attrs,text){{const e=document.createElementNS(NS,tag);for(const k in attrs)e.setAttribute(k,attrs[k]);if(text)e.textContent=text;svg.appendChild(e);return e;}}
add('rect',{{x:0,y:0,width:W,height:H,fill:'#fff'}});
add('line',{{x1:ml,y1:mt,x2:ml,y2:mt+ph,stroke:'#333'}});
add('line',{{x1:ml+pw,y1:mt,x2:ml+pw,y2:mt+ph,stroke:'#333'}});
add('line',{{x1:ml,y1:mt+ph,x2:ml+pw,y2:mt+ph,stroke:'#333'}});
add('text',{{x:20,y:35,fill:'#1565c0','font-size':14}},'贵州茅台收盘价（元）');
add('text',{{x:W-170,y:35,fill:'#c62828','font-size':14}},'飞天茅台53度散瓶参考价（元/瓶）');
let p1='',p2='';
for(let i=0;i<dates.length;i++){{p1+=`${{x(i)}},${{yS(stock[i])}} `;p2+=`${{x(i)}},${{yL(liquor[i])}} `;}}
add('polyline',{{points:p1.trim(),fill:'none',stroke:'#1565c0','stroke-width':2}});
add('polyline',{{points:p2.trim(),fill:'none',stroke:'#c62828','stroke-width':2}});
const latest=dates.length-1;
add('circle',{{cx:x(latest),cy:yS(stock[latest]),r:4,fill:'#1565c0'}});
add('text',{{x:x(latest)-10,y:yS(stock[latest])-12,fill:'#1565c0','font-size':12}},`最新股价: ${{stock[latest].toFixed(2)}}`);
add('circle',{{cx:x(latest),cy:yL(liquor[latest]),r:4,fill:'#c62828'}});
add('text',{{x:x(latest)-10,y:yL(liquor[latest])+18,fill:'#c62828','font-size':12}},`最新酒价: ${{liquor[latest].toFixed(2)}}`);
const hoverLine=add('line',{{x1:ml,y1:mt,x2:ml,y2:mt+ph,stroke:'#999','stroke-dasharray':'4 3',visibility:'hidden'}});
svg.addEventListener('mousemove',(ev)=>{{
 const r=svg.getBoundingClientRect();
 const mx=ev.clientX-r.left;
 if(mx<ml||mx>ml+pw)return;
 const idx=Math.round((mx-ml)/pw*(dates.length-1));
 hoverLine.setAttribute('x1',x(idx)); hoverLine.setAttribute('x2',x(idx)); hoverLine.setAttribute('visibility','visible');
 document.getElementById('tip').textContent=`日期: ${{dates[idx]}} ｜ 贵州茅台收盘价: ${{stock[idx].toFixed(2)}} 元 ｜ 飞天茅台53度散瓶参考价: ${{liquor[idx].toFixed(2)}} 元/瓶`;
}});
svg.addEventListener('mouseleave',()=>{{hoverLine.setAttribute('visibility','hidden');document.getElementById('tip').textContent='移动鼠标查看同一日期的股价与酒价';}});
</script>
</body>
</html>'''

    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(html, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="贵州茅台股价 vs 飞天茅台53度散瓶参考价 双轴图")
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--end", default=date.today().strftime(DATE_FMT))
    parser.add_argument("--stock-csv", type=Path, default=Path("data/stock_prices_auto.csv"))
    parser.add_argument("--liquor-csv", type=Path, default=Path("data/moutai_liquor_prices.csv"))
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
