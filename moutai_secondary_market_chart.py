from __future__ import annotations

import argparse
import csv
import json
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DATE_FMT = "%Y-%m-%d"


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
    payload = json.loads(_http_get(url))
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
    if not stock_csv.exists():
        if not auto_fetch:
            raise FileNotFoundError(f"Stock CSV not found: {stock_csv}")
        fetch_stock_data_from_eastmoney(start, end, stock_csv)

    rows = []
    with stock_csv.open("r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        if not r.fieldnames or "date" not in r.fieldnames or "close" not in r.fieldnames:
            raise ValueError(f"Stock CSV must include ['date','close'], got {r.fieldnames}")
        for row in r:
            try:
                d = _parse_date(row["date"])
                if d < start or d > end:
                    continue
                rows.append({"date": d.strftime(DATE_FMT), "close": float(row["close"])})
            except Exception:
                continue

    rows.sort(key=lambda x: x["date"])
    dedup = {}
    for row in rows:
        dedup[row["date"]] = row
    stock = [dedup[k] for k in sorted(dedup.keys())]
    if not stock:
        raise RuntimeError("Stock data is empty after filtering; please check csv or date range")
    return stock


def generate_interactive_html(stock_rows: list[dict], output_html: Path) -> None:
    sample_csv = """date,original_box_price,bulk_price
2021-03-03,3350,3150
2021-03-04,3350,3150
2021-03-05,3330,3120"""

    html = f"""<!doctype html>
<html lang=\"zh-CN\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>贵州茅台股价与飞天茅台53度参考价（粘贴CSV绘图）</title>
  <script src=\"https://cdn.plot.ly/plotly-2.35.2.min.js\"></script>
  <style>
    body {{ font-family: -apple-system,BlinkMacSystemFont,\"Segoe UI\",\"PingFang SC\",\"Microsoft YaHei\",sans-serif; margin: 0; padding: 18px; background: #fff; color: #222; }}
    h2 {{ margin: 0 0 6px; }}
    .sub {{ color: #666; margin-bottom: 12px; }}
    .note {{ background: #f8f9fb; border: 1px solid #e9edf2; border-radius: 8px; padding: 10px 12px; margin-bottom: 10px; font-size: 14px; }}
    textarea {{ width: 100%; min-height: 180px; border: 1px solid #d9d9d9; border-radius: 8px; padding: 10px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px; box-sizing: border-box; }}
    .actions {{ margin: 10px 0 8px; display: flex; gap: 8px; flex-wrap: wrap; }}
    button {{ border: 1px solid #d0d7de; background: #fff; border-radius: 8px; padding: 8px 12px; cursor: pointer; }}
    button.primary {{ background: #1565c0; border-color: #1565c0; color: #fff; }}
    #error {{ color: #b42318; font-size: 13px; min-height: 18px; margin-bottom: 8px; white-space: pre-wrap; }}
    #chart {{ width: 100%; height: 680px; }}
  </style>
</head>
<body>
  <h2>贵州茅台股价与飞天茅台53度散瓶市场参考价走势对比</h2>
  <div class=\"sub\">时间范围：2018-01-01 至今（按A股交易日对齐）｜酒价数据来源：用户提供的批发参考价整理表（CSV）</div>
  <div class=\"note\">将包含 <code>date, original_box_price, bulk_price</code> 三列的 CSV 内容粘贴到下方后，点击“加载并绘图”。</div>

  <textarea id=\"csvInput\" placeholder=\"请粘贴CSV，例如：\ndate,original_box_price,bulk_price\n2021-03-03,3350,3150\n2021-03-04,3350,3150\"></textarea>
  <div class=\"actions\">
    <button class=\"primary\" id=\"loadBtn\">加载并绘图</button>
    <button id=\"clearBtn\">清空数据</button>
    <button id=\"sampleBtn\">加载示例CSV</button>
  </div>
  <div id=\"error\"></div>
  <div id=\"chart\"></div>

<script>
const stock = {json.dumps(stock_rows, ensure_ascii=False)};
const sampleCsv = {json.dumps(sample_csv, ensure_ascii=False)};

function parseCsvText(text) {{
  const lines = text.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
  if (lines.length < 2) throw new Error('CSV 内容为空或缺少数据行。');
  const headers = lines[0].split(',').map(x => x.trim());
  if (!headers.includes('date')) throw new Error('缺少 date 列');
  if (!headers.includes('bulk_price')) throw new Error('缺少 bulk_price 列');
  if (!headers.includes('original_box_price')) throw new Error('缺少 original_box_price 列');

  const idxDate = headers.indexOf('date');
  const idxOrig = headers.indexOf('original_box_price');
  const idxBulk = headers.indexOf('bulk_price');

  const map = new Map();
  for (let i = 1; i < lines.length; i++) {{
    const cols = lines[i].split(',').map(x => x.trim());
    if (cols.length === 1 && cols[0] === '') continue;
    const dstr = cols[idxDate];
    const d = new Date(dstr + 'T00:00:00');
    if (!dstr || isNaN(d.getTime())) throw new Error(`日期格式错误（第 ${{i+1}} 行）`);

    const bstr = cols[idxBulk] ?? '';
    const ostr = cols[idxOrig] ?? '';
    const bulk = bstr === '' ? null : Number(bstr);
    const original = ostr === '' ? null : Number(ostr);
    if (bstr !== '' && Number.isNaN(bulk)) throw new Error(`bulk_price 数值解析失败（第 ${{i+1}} 行）`);
    if (ostr !== '' && Number.isNaN(original)) throw new Error(`original_box_price 数值解析失败（第 ${{i+1}} 行）`);

    const key = dstr;
    map.set(key, {{ date: key, bulk_price: bulk, original_box_price: original }}); // 同日保留最后一条
  }}

  const rows = Array.from(map.values()).sort((a, b) => a.date.localeCompare(b.date));
  if (!rows.length) throw new Error('没有可用酒价数据。');
  return rows;
}}

function ema(values, span) {{
  const alpha = 2 / (span + 1);
  const out = [];
  let prev = null;
  for (const v of values) {{
    prev = (prev === null) ? v : alpha * v + (1 - alpha) * prev;
    out.push(prev);
  }}
  return out;
}}

function computeRunupAndDrawdown(closes) {{
  let minv = closes[0], maxUp = -1;
  let runMax = closes[0], maxDd = 0;
  for (const c of closes) {{
    if (c < minv) minv = c;
    const up = c / minv - 1;
    if (up > maxUp) maxUp = up;
    if (c > runMax) runMax = c;
    const dd = c / runMax - 1;
    if (dd < maxDd) maxDd = dd;
  }}
  return {{ maxUp, maxDd }};
}}

function mergeByStockDays(stockRows, liquorRows) {{
  let i = 0;
  let lastBulk = null;
  let lastOrig = null;
  const merged = [];

  for (const s of stockRows) {{
    while (i < liquorRows.length && liquorRows[i].date <= s.date) {{
      if (liquorRows[i].bulk_price !== null) lastBulk = liquorRows[i].bulk_price;
      if (liquorRows[i].original_box_price !== null) lastOrig = liquorRows[i].original_box_price;
      i++;
    }}
    merged.push({{
      date: s.date,
      close: s.close,
      bulk_price: lastBulk,
      original_box_price: lastOrig
    }});
  }}
  return merged;
}}

function draw(merged) {{
  const dates = merged.map(x => x.date);
  const close = merged.map(x => x.close);
  const bulk = merged.map(x => x.bulk_price);
  const orig = merged.map(x => x.original_box_price);
  const ema20 = ema(close, 20);
  const ema55 = ema(close, 55);
  const ema100 = ema(close, 100);
  const ema200 = ema(close, 200);
  const stats = computeRunupAndDrawdown(close);

  const traces = [
    {{ x: dates, y: close, name: '贵州茅台股价', type: 'scatter', mode: 'lines', line: {{color:'#1565c0', width:2.2}}, yaxis:'y',
       hovertemplate:'日期：%{{x|%Y年%m月%d日}}<br>贵州茅台股价：%{{y:.2f}} 元<extra></extra>' }},
    {{ x: dates, y: bulk, name: '飞天茅台53度当年散装参考价', type: 'scatter', mode: 'lines', line: {{color:'#c62828', width:2.2}}, yaxis:'y2',
       hovertemplate:'日期：%{{x|%Y年%m月%d日}}<br>飞天茅台53度当年散装参考价：%{{y:.2f}} 元/瓶<extra></extra>' }},
    {{ x: dates, y: orig, name: '飞天茅台53度当年原装参考价', type: 'scatter', mode: 'lines', line: {{color:'#ef6c00', width:1.8, dash:'dot'}}, yaxis:'y2', visible:'legendonly',
       hovertemplate:'日期：%{{x|%Y年%m月%d日}}<br>飞天茅台53度当年原装参考价：%{{y:.2f}} 元/瓶<extra></extra>' }},
    {{ x: dates, y: ema20, name: 'EMA20', type: 'scatter', mode: 'lines', line: {{color:'#42a5f5', width:1.1}}, visible:'legendonly', yaxis:'y' }},
    {{ x: dates, y: ema55, name: 'EMA55', type: 'scatter', mode: 'lines', line: {{color:'#26a69a', width:1.1}}, visible:'legendonly', yaxis:'y' }},
    {{ x: dates, y: ema100, name: 'EMA100', type: 'scatter', mode: 'lines', line: {{color:'#ab47bc', width:1.1}}, visible:'legendonly', yaxis:'y' }},
    {{ x: dates, y: ema200, name: 'EMA200', type: 'scatter', mode: 'lines', line: {{color:'#8d6e63', width:1.1}}, visible:'legendonly', yaxis:'y' }}
  ];

  const latestBulkIdx = (() => {{
    for (let i = bulk.length - 1; i >= 0; i--) if (bulk[i] !== null && bulk[i] !== undefined) return i;
    return -1;
  }})();

  const annotations = [
    {{ xref:'paper', yref:'paper', x:0.985, y:0.985, xanchor:'right', yanchor:'top', showarrow:false,
       text:`最大涨幅：+${{(stats.maxUp*100).toFixed(2)}}%<br>最大回撤：${{(stats.maxDd*100).toFixed(2)}}%`,
       font:{{size:12,color:'#333'}}, bgcolor:'rgba(255,255,255,0.88)', bordercolor:'rgba(0,0,0,0.2)', borderwidth:1 }},
    {{ x: dates[dates.length-1], y: close[close.length-1], yref:'y', text:`最新股价: ${{close[close.length-1].toFixed(2)}}`,
       showarrow:true, arrowhead:2, ax:22, ay:-28, font:{{color:'#1565c0', size:12}}, bgcolor:'rgba(255,255,255,0.75)' }}
  ];
  if (latestBulkIdx >= 0) {{
    annotations.push({{ x: dates[latestBulkIdx], y: bulk[latestBulkIdx], yref:'y2', text:`最新散装价: ${{bulk[latestBulkIdx].toFixed(2)}}`,
       showarrow:true, arrowhead:2, ax:22, ay:28, font:{{color:'#c62828', size:12}}, bgcolor:'rgba(255,255,255,0.75)' }});
  }}

  Plotly.newPlot('chart', traces, {{
    title: '贵州茅台股价与飞天茅台53度散瓶市场参考价走势对比<br><sup>时间范围：2018-01-01 至今（按A股交易日对齐）｜酒价数据来源：用户提供的批发参考价整理表（CSV）</sup>',
    xaxis: {{ title:'日期', type:'date', tickformat:'%Y年%m月', hoverformat:'%Y年%m月%d日', showgrid:false }},
    yaxis: {{ title:'贵州茅台收盘价（元）', side:'left', showgrid:false }},
    yaxis2: {{ title:'飞天茅台53度参考价（元/瓶）', overlaying:'y', side:'right', showgrid:false }},
    hovermode:'x unified',
    template:'plotly_white',
    plot_bgcolor:'white',
    paper_bgcolor:'white',
    legend: {{orientation:'h', yanchor:'bottom', y:1.02, xanchor:'left', x:0}},
    margin: {{l:70, r:70, t:110, b:60}},
    annotations
  }}, {{responsive:true}});
}}

const errorEl = document.getElementById('error');
const inputEl = document.getElementById('csvInput');

function showError(msg) {{ errorEl.textContent = msg || ''; }}

document.getElementById('sampleBtn').addEventListener('click', () => {{
  inputEl.value = sampleCsv;
  showError('');
}});

document.getElementById('clearBtn').addEventListener('click', () => {{
  inputEl.value = '';
  showError('');
  Plotly.purge('chart');
}});

document.getElementById('loadBtn').addEventListener('click', () => {{
  try {{
    showError('');
    const liquorRows = parseCsvText(inputEl.value);
    const merged = mergeByStockDays(stock, liquorRows); // left join by stock days
    draw(merged);
  }} catch (e) {{
    showError(String(e.message || e));
  }}
}});
</script>
</body>
</html>
"""

    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(html, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="生成可粘贴CSV并交互绘图的茅台双轴HTML")
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--end", default=date.today().strftime(DATE_FMT))
    parser.add_argument("--stock-csv", type=Path, default=Path("data/stock_prices_auto.csv"))
    parser.add_argument("--output-html", type=Path, default=Path("output/moutai_stock_vs_liquor_interactive.html"))
    parser.add_argument("--no-auto-fetch-stock", action="store_true", help="Do not auto-fetch stock data if stock csv missing")
    args = parser.parse_args()

    start = _parse_date(args.start)
    end = _parse_date(args.end)

    stock_rows = load_stock_data(start, end, args.stock_csv, auto_fetch=not args.no_auto_fetch_stock)
    print(f"[DEBUG] stock rows: {len(stock_rows)}")
    print(f"[DEBUG] stock date min/max: {stock_rows[0]['date']} / {stock_rows[-1]['date']}")

    generate_interactive_html(stock_rows, args.output_html)
    print(f"[OK] interactive html: {args.output_html}")


if __name__ == "__main__":
    main()
