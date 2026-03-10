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
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

DATE_FMT = "%Y-%m-%d"
DEFAULT_LIQUOR_WEB_SOURCES = [
    "https://www.todayjiu.com/",
    "https://www.jiuxiwang.cn/",
    "https://www.jiuchacha.com/",
]


@dataclass
class Record:
    date: datetime
    value: Optional[float]


@dataclass
class ArticleIndexItem:
    title: str
    date: str
    article_url: str
    jump_params: str


def http_get(url: str, timeout: int = 20) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def _as_date(value: object) -> str:
    if isinstance(value, int):
        return datetime.fromtimestamp(value).strftime(DATE_FMT)
    s = str(value or "").strip()
    if not s:
        return ""
    for fmt in ["%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d"]:
        try:
            return datetime.strptime(s, fmt).strftime(DATE_FMT)
        except ValueError:
            pass
    if s.isdigit() and len(s) >= 10:
        try:
            return datetime.fromtimestamp(int(s[:10])).strftime(DATE_FMT)
        except ValueError:
            return ""
    return ""


def _extract_params_from_url(url: str) -> str:
    q = parse_qs(urlparse(url).query)
    keys = ["__biz", "mid", "idx", "sn", "album_id"]
    out = []
    for k in keys:
        if k in q and q[k]:
            out.append(f"{k}={q[k][0]}")
    return "&".join(out)


def extract_article_index_from_history_page(index_text: str) -> List[ArticleIndexItem]:
    """Extract title/date/url from WeChat history list page response.

    Supports:
    1) JSON payload style (getalbum_resp / article_list)
    2) HTML fallback using regex
    """
    items: List[ArticleIndexItem] = []

    # JSON style
    try:
        payload = json.loads(index_text)
    except json.JSONDecodeError:
        payload = None

    if isinstance(payload, dict):
        candidate_lists: List[List[dict]] = []
        for key in ["article_list", "items", "list"]:
            if isinstance(payload.get(key), list):
                candidate_lists.append(payload[key])

        getalbum = payload.get("getalbum_resp")
        if isinstance(getalbum, dict):
            for key in ["article_list", "item", "items", "list"]:
                if isinstance(getalbum.get(key), list):
                    candidate_lists.append(getalbum[key])
            nested = getalbum.get("getalbum_resp")
            if isinstance(nested, dict):
                for key in ["article_list", "item", "items", "list"]:
                    if isinstance(nested.get(key), list):
                        candidate_lists.append(nested[key])

        for arr in candidate_lists:
            for it in arr:
                if not isinstance(it, dict):
                    continue
                title = str(it.get("title") or it.get("name") or "").strip()
                date = _as_date(it.get("update_time") or it.get("create_time") or it.get("datetime") or it.get("date"))
                url = str(it.get("url") or it.get("link") or it.get("content_url") or "").strip()
                params = _extract_params_from_url(url)
                if title:
                    items.append(ArticleIndexItem(title=title, date=date, article_url=url, jump_params=params))

    # HTML fallback
    if not items:
        title_pat = re.compile(r'title\s*[:=]\s*["\']([^"\']{4,120})["\']', re.I)
        date_pat = re.compile(r'(20\d{2}[-/.]\d{1,2}[-/.]\d{1,2})')
        url_pat = re.compile(r'https://mp\.weixin\.qq\.com/s\?[^"\'\s<]+')
        titles = title_pat.findall(index_text)
        urls = url_pat.findall(index_text)
        dates = [d.replace("/", "-").replace(".", "-") for d in date_pat.findall(index_text)]
        n = min(len(titles), len(urls), max(len(titles), len(urls), len(dates)))
        for i in range(n):
            title = titles[i] if i < len(titles) else ""
            url = urls[i] if i < len(urls) else ""
            date = _as_date(dates[i]) if i < len(dates) else ""
            if title:
                items.append(ArticleIndexItem(title=title, date=date, article_url=url, jump_params=_extract_params_from_url(url)))

    # de-dup
    seen = set()
    dedup: List[ArticleIndexItem] = []
    for it in items:
        key = (it.title, it.date, it.article_url)
        if key in seen:
            continue
        seen.add(key)
        dedup.append(it)
    return dedup


def save_article_index_csv(path: Path, items: List[ArticleIndexItem], source_url: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["title", "date", "article_url", "jump_params", "index_source"])
        for it in items:
            w.writerow([it.title, it.date, it.article_url, it.jump_params, source_url])


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
            w.writerow(row.split(","))

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "close"])
        for row in klines:
            p = row.split(",")
            w.writerow([p[0], p[2]])


def extract_liquor_records_from_generic_html(html: str, source_url: str) -> List[Tuple[str, str, float, str]]:
    """Parse generic web pages for daily quote tuples(date,title,price,snippet)."""
    include = ["茅台", "26", "飞天"]
    exclude = ["生肖", "礼盒", "年份酒", "整箱"]
    rows: List[Tuple[str, str, float, str]] = []

    # pattern: title ... date ... price
    pat = re.compile(
        r"(?P<title>[\u4e00-\u9fa5A-Za-z0-9（）()·\-\s]{6,120})[^\n]{0,200}?"
        r"(?P<date>20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}|20\d{6})[^\n]{0,120}?"
        r"(?:¥|￥|RMB)?\s*(?P<price>\d{3,6}(?:\.\d{1,2})?)"
    )
    for m in pat.finditer(html):
        title = re.sub(r"\s+", "", m.group("title"))
        if not all(k in title for k in include):
            continue
        if any(k in title for k in exclude):
            continue
        date = _as_date(m.group("date").replace("/", "-").replace(".", "-"))
        if not date:
            continue
        price = float(m.group("price"))
        if not (500 <= price <= 100000):
            continue
        snippet = html[max(0, m.start() - 50):m.end() + 50].replace("\n", " ")
        rows.append((date, title, price, snippet))

    # json-like fallback
    if not rows:
        jpat = re.compile(
            r'"title"\s*:\s*"(?P<title>[^"]{4,120})".{0,220}?'
            r'"(?:date|pub_time|create_time)"\s*:\s*"?(?P<date>[^",}]{6,20})"?.{0,120}?'
            r'"(?:price|value|quote)"\s*:\s*"?(?P<price>\d{3,6}(?:\.\d{1,2})?)"?',
            re.S
        )
        for m in jpat.finditer(html):
            title = re.sub(r"\s+", "", m.group("title"))
            if not all(k in title for k in include):
                continue
            if any(k in title for k in exclude):
                continue
            date = _as_date(m.group("date"))
            if not date:
                continue
            price = float(m.group("price"))
            if not (500 <= price <= 100000):
                continue
            snippet = f"json:{title}"
            rows.append((date, title, price, snippet))

    # add source in snippet context by caller
    return rows


def fetch_liquor_prices_from_web_sources(
    start: str,
    end: str,
    output_csv: Path,
    raw_csv: Path,
    index_csv: Path,
    product_name: str,
    caliber: str,
    source_urls: List[str],
) -> None:
    start_d = datetime.strptime(start, DATE_FMT)
    end_d = datetime.strptime(end, DATE_FMT)

    source_stats: List[Tuple[str, int, str]] = []
    all_rows: List[Tuple[str, str, str, float, str]] = []

    for url in source_urls:
        try:
            html = http_get(url)
            rows = extract_liquor_records_from_generic_html(html, url)
            rows = [r for r in rows if start_d <= datetime.strptime(r[0], DATE_FMT) <= end_d]
            source_stats.append((url, len(rows), "ok"))
            for d, title, price, snip in rows:
                all_rows.append((d, title, url, price, snip))
        except Exception as e:
            source_stats.append((url, 0, f"fail:{e}"))

    index_csv.parent.mkdir(parents=True, exist_ok=True)
    with index_csv.open("w", encoding="utf-8", newline="") as f:
        w=csv.writer(f)
        w.writerow(["source_url", "matched_rows", "status"])
        for u, n, st in source_stats:
            w.writerow([u, n, st])

    if not all_rows:
        raise RuntimeError("酒价抓取失败：多个公开网页源均未提取到有效日度记录。请检查网络(403)或调整抓取源。")

    raw_csv.parent.mkdir(parents=True, exist_ok=True)
    with raw_csv.open("w", encoding="utf-8", newline="") as f:
        w=csv.writer(f)
        w.writerow(["date", "title", "source_url", "price", "caliber", "snippet"])
        for r in all_rows:
            w.writerow([r[0], r[1], r[2], r[3], caliber, r[4]])

    by_day: Dict[str, List[float]] = {}
    for d, _, _, p, _ in all_rows:
        by_day.setdefault(d, []).append(p)

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8", newline="") as f:
        w=csv.writer(f)
        w.writerow(["date", "secondary_price", "source", "product", "caliber", "aggregation", "quote_count"])
        for d in sorted(by_day):
            prices=sorted(by_day[d])
            w.writerow([d, median(prices), "multi-web-sources", product_name, caliber, "median", len(prices)])


def extract_prices_from_article_html(html: str) -> List[float]:
    """Extract potential liquor prices from article body text snippets."""
    prices: List[float] = []
    # restrict to snippets mentioning maotai + 26 + 飞天
    snippet_pat = re.compile(r"[^\n]{0,80}(茅台[^\n]{0,80}26[^\n]{0,80}飞天|飞天[^\n]{0,80}26[^\n]{0,80}茅台)[^\n]{0,120}", re.I)
    for snip in snippet_pat.findall(html):
        for n in re.findall(r"(?:¥|￥|RMB)?\s*(\d{3,6}(?:\.\d{1,2})?)\s*元?", snip):
            v = float(n)
            if 500 <= v <= 100000:
                prices.append(v)

    # fallback: global price patterns with product keywords nearby
    if not prices:
        for m in re.finditer(r"(?:¥|￥|RMB)?\s*(\d{3,6}(?:\.\d{1,2})?)\s*元?", html):
            window = html[max(0, m.start() - 100):m.end() + 100]
            if all(k in window for k in ["茅台", "26", "飞天"]):
                v = float(m.group(1))
                if 500 <= v <= 100000:
                    prices.append(v)
    return prices


def fetch_liquor_prices_from_history_index(
    start: str,
    end: str,
    output_csv: Path,
    raw_csv: Path,
    index_csv: Path,
    history_index_url: str,
    product_name: str,
    caliber: str,
    max_articles: int,
) -> None:
    include = ["茅台", "26", "飞天"]
    exclude = ["生肖", "礼盒", "年份酒", "整箱"]

    try:
        index_text = http_get(history_index_url)
    except Exception as e:
        raise RuntimeError(f"酒价索引页抓取失败（公众号历史列表页不可访问）: {e}") from e

    index_items = extract_article_index_from_history_page(index_text)
    if not index_items:
        raise RuntimeError(
            "索引页解析失败：未提取到文章标题/日期/跳转信息。"
            "请提供能返回列表JSON或包含文章链接参数(mid/idx/sn)的页面响应。"
        )

    save_article_index_csv(index_csv, index_items, history_index_url)

    # only keep target article titles in date range
    start_d = datetime.strptime(start, DATE_FMT)
    end_d = datetime.strptime(end, DATE_FMT)
    target_items: List[ArticleIndexItem] = []
    for it in index_items:
        if not it.date:
            continue
        d = datetime.strptime(it.date, DATE_FMT)
        if not (start_d <= d <= end_d):
            continue
        t = it.title
        if not all(k in t for k in include):
            continue
        if any(k in t for k in exclude):
            continue
        target_items.append(it)

    if not target_items:
        raise RuntimeError("索引页中未筛到符合规则的目标文章（茅台+26+飞天，排除生肖/礼盒/年份酒/整箱）")

    rows: List[Tuple[str, str, str, float, str]] = []
    missing_urls = 0
    for it in target_items[:max_articles]:
        if not it.article_url:
            missing_urls += 1
            continue
        try:
            html = http_get(it.article_url)
        except Exception as e:
            print(f"[WARN] 正文抓取失败: {it.title} -> {e}")
            continue

        prices = extract_prices_from_article_html(html)
        if not prices:
            continue
        for p in prices:
            snippet = f"title={it.title}; params={it.jump_params}"
            rows.append((it.date, it.title, it.article_url, p, snippet))

    if missing_urls and not rows:
        raise RuntimeError(
            f"索引页已提取到文章清单，但缺少可直达URL（{missing_urls}篇）。"
            "至少需要每篇文章的可跳转URL，或可拼接URL所需参数(__biz/mid/idx/sn)。"
        )

    if not rows:
        raise RuntimeError(
            "未在目标文章正文中解析到价格。可能原因：正文是图片/表格、反爬、或价格表达方式不匹配正则。"
        )

    raw_csv.parent.mkdir(parents=True, exist_ok=True)
    with raw_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "title", "article_url", "price", "source", "caliber", "snippet"])
        for d, title, url, price, snip in rows:
            w.writerow([d, title, url, price, history_index_url, caliber, snip])

    day_prices: Dict[str, List[float]] = {}
    for d, _, _, p, _ in rows:
        day_prices.setdefault(d, []).append(p)

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "secondary_price", "source", "product", "caliber", "aggregation", "quote_count"])
        for d in sorted(day_prices):
            prices = sorted(day_prices[d])
            w.writerow([d, median(prices), history_index_url, product_name, caliber, "median", len(prices)])


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

    dates = sorted(set(lmap) & set(smap)) if align_mode == "inner" else sorted(set(lmap))
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

    p1, p2, labels = [], [], []
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
            pix[i:i + 3] = bytes(c)

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
        line(*p1[i - 1], *p1[i], (198, 40, 40))
        line(*p2[i - 1], *p2[i], (21, 101, 192))

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
    p.add_argument("--fetch-liquor", action="store_true", help="从公众号历史列表页批量构建文章清单并抓正文解析价格")
    p.add_argument("--start", default="2025-01-01")
    p.add_argument("--end", default=datetime.now().strftime(DATE_FMT))
    p.add_argument("--stock-csv", type=Path, default=Path("data/stock_prices_auto.csv"))
    p.add_argument("--stock-raw-csv", type=Path, default=Path("data/stock_prices_auto_raw.csv"))
    p.add_argument("--liquor-csv", type=Path, default=Path("data/liquor_prices_auto.csv"))
    p.add_argument("--liquor-raw-csv", type=Path, default=Path("data/liquor_prices_auto_raw.csv"))
    p.add_argument("--liquor-index-csv", type=Path, default=Path("data/liquor_article_index.csv"))
    p.add_argument("--history-index-url", default="https://mp.weixin.qq.com/mp/appmsgalbum?__biz=Mzk0NzI1MjY4Ng==&action=getalbum&album_id=4328328528879271941&scene=126")
    p.add_argument("--liquor-fetch-mode", choices=["auto", "web", "history"], default="auto")
    p.add_argument("--liquor-web-sources", default=",".join(DEFAULT_LIQUOR_WEB_SOURCES), help="逗号分隔的公开网页源列表")
    p.add_argument("--product-name", default="贵州茅台酒（26年飞天茅台）")
    p.add_argument("--price-caliber", default="散瓶/单瓶口径")
    p.add_argument("--max-articles", type=int, default=200)
    p.add_argument("--align-mode", choices=["inner", "left"], default="inner")
    p.add_argument("--fill-mode", choices=["none", "ffill"], default="none")
    p.add_argument("--output-prefix", default="output/moutai_auto")
    args = p.parse_args()

    if args.fetch_stock:
        fetch_stock_prices(args.start, args.end, args.stock_csv, args.stock_raw_csv)
        print(f"[OK] 已抓取A股数据 -> {args.stock_csv}")

    if args.fetch_liquor:
        srcs = [x.strip() for x in args.liquor_web_sources.split(",") if x.strip()]
        if args.liquor_fetch_mode == "web":
            fetch_liquor_prices_from_web_sources(args.start, args.end, args.liquor_csv, args.liquor_raw_csv, args.liquor_index_csv, args.product_name, args.price_caliber, srcs)
        elif args.liquor_fetch_mode == "history":
            fetch_liquor_prices_from_history_index(
                args.start,
                args.end,
                args.liquor_csv,
                args.liquor_raw_csv,
                args.liquor_index_csv,
                args.history_index_url,
                args.product_name,
                args.price_caliber,
                args.max_articles,
            )
        else:
            web_err = None
            try:
                fetch_liquor_prices_from_web_sources(args.start, args.end, args.liquor_csv, args.liquor_raw_csv, args.liquor_index_csv, args.product_name, args.price_caliber, srcs)
            except Exception as e:
                web_err = e
                print(f"[WARN] 网页源抓取失败，转历史索引模式: {e}")
                fetch_liquor_prices_from_history_index(
                    args.start,
                    args.end,
                    args.liquor_csv,
                    args.liquor_raw_csv,
                    args.liquor_index_csv,
                    args.history_index_url,
                    args.product_name,
                    args.price_caliber,
                    args.max_articles,
                )
            if web_err is None:
                print("[OK] 酒价来源模式: web")
            else:
                print("[OK] 酒价来源模式: history(自动回退)")
        print(f"[OK] 已抓取酒价数据 -> {args.liquor_csv}")
        print(f"[OK] 已保存索引/源状态 -> {args.liquor_index_csv}")

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
