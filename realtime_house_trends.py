#!/usr/bin/env python3
"""抓取重点城市二手房成交均价与挂牌量，输出 CSV + HTML + JSON。"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import statistics
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

CITY_DOMAIN = {
    "北京": "bj",
    "上海": "sh",
    "广州": "gz",
    "深圳": "sz",
    "成都": "cd",
    "武汉": "wh",
    "杭州": "hz",
    "香港": "hk",
}

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class CitySnapshot:
    city: str
    listing_count: int | None
    recent_deal_avg_price: float | None


def fetch(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def parse_listing_count(html: str) -> int | None:
    for pat in (r"共找到\s*([0-9,]+)\s*套", r"total\s*:\s*([0-9,]+)"):
        m = re.search(pat, html)
        if m:
            return int(m.group(1).replace(",", ""))
    return None


def parse_monthly_deal_price(html: str) -> dict[str, float]:
    unit_prices = [int(x) for x in re.findall(r"单价\s*([0-9]{3,7})\s*元/平", html)]
    deal_dates = re.findall(r"成交日期\s*([0-9]{4})\.([0-9]{1,2})\.([0-9]{1,2})", html)

    if not unit_prices:
        unit_prices = [int(x) for x in re.findall(r"unitPrice[^0-9]*([0-9]{3,7})\s*元/平", html)]
    if not deal_dates:
        deal_dates = re.findall(r"dealDate[^0-9]*([0-9]{4})\.([0-9]{1,2})\.([0-9]{1,2})", html)

    bucket: dict[str, list[int]] = defaultdict(list)
    for i, (year, month, _day) in enumerate(deal_dates):
        if i >= len(unit_prices):
            break
        bucket[f"{year}-{int(month):02d}"].append(unit_prices[i])

    return {k: round(statistics.mean(v), 2) for k, v in sorted(bucket.items())}


def collect_city_data(city: str, code: str) -> tuple[CitySnapshot, dict[str, float]]:
    root = f"https://{code}.lianjia.com"
    listing_count = None
    monthly_deal: dict[str, float] = {}

    try:
        listing_count = parse_listing_count(fetch(f"{root}/ershoufang/"))
    except urllib.error.URLError:
        pass

    try:
        monthly_deal = parse_monthly_deal_price(fetch(f"{root}/chengjiao/"))
    except urllib.error.URLError:
        pass

    recent = next(reversed(monthly_deal.values())) if monthly_deal else None
    return CitySnapshot(city, listing_count, recent), monthly_deal


def append_snapshots(path: Path, snapshots: list[CitySnapshot], run_date: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["date", "city", "listing_count", "recent_deal_avg_price"])
        for s in snapshots:
            w.writerow([run_date, s.city, s.listing_count, s.recent_deal_avg_price])


def load_listing_history(path: Path) -> dict[str, list[tuple[str, int]]]:
    history: dict[str, list[tuple[str, int]]] = defaultdict(list)
    if not path.exists():
        return history
    with path.open("r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["listing_count"] in ("", "None"):
                continue
            history[row["city"]].append((row["date"], int(row["listing_count"])))
    for city in history:
        history[city].sort(key=lambda x: x[0])
    return history


def render_html(all_monthly_deals: dict[str, dict[str, float]], history: dict[str, list[tuple[str, int]]], out_file: Path) -> None:
    deal_labels = sorted({m for v in all_monthly_deals.values() for m in v.keys()})
    listing_labels = sorted({d for pts in history.values() for d, _ in pts})

    deal_datasets = [{"label": c, "data": [m.get(x) for x in deal_labels]} for c, m in all_monthly_deals.items()]
    listing_datasets = []
    for city, points in history.items():
        dm = {d: v for d, v in points}
        listing_datasets.append({"label": city, "data": [dm.get(x) for x in listing_labels]})

    html = f"""<!doctype html><html lang='zh-CN'><head><meta charset='UTF-8'/>
<title>重点城市二手房走势</title><script src='https://cdn.jsdelivr.net/npm/chart.js'></script>
<style>body{{font-family:Arial;margin:24px}}canvas{{max-width:1100px;max-height:420px}}</style></head><body>
<h1>重点城市二手房走势（实时抓取快照）</h1><h2>成交均价走势（元/平）</h2><canvas id='deal'></canvas>
<h2>挂牌量走势（套）</h2><canvas id='listing'></canvas><script>
const dealLabels={json.dumps(deal_labels, ensure_ascii=False)};
const dealDatasets={json.dumps(deal_datasets, ensure_ascii=False)};
const listingLabels={json.dumps(listing_labels, ensure_ascii=False)};
const listingDatasets={json.dumps(listing_datasets, ensure_ascii=False)};
const palette=['#3366cc','#dc3912','#ff9900','#109618','#990099','#0099c6','#dd4477','#66aa00'];
const build=(raw)=>raw.map((d,i)=>({{label:d.label,data:d.data,borderColor:palette[i%palette.length],tension:0.2,spanGaps:true}}));
new Chart(document.getElementById('deal'),{{type:'line',data:{{labels:dealLabels,datasets:build(dealDatasets)}}}});
new Chart(document.getElementById('listing'),{{type:'line',data:{{labels:listingLabels,datasets:build(listingDatasets)}}}});
</script></body></html>"""
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(html, encoding="utf-8")


def write_latest_json(
    snapshots: list[CitySnapshot],
    all_monthly_deals: dict[str, dict[str, float]],
    history: dict[str, list[tuple[str, int]]],
    out_file: Path,
) -> None:
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(
        json.dumps(
            {
                "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "cities": [asdict(s) for s in snapshots],
                "monthly_deals": all_monthly_deals,
                "listing_history": {k: [{"date": d, "value": v} for d, v in pts] for k, pts in history.items()},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def run(snapshot_file: Path, output_html: Path, output_json: Path) -> dict:
    run_date = dt.date.today().isoformat()
    snapshots: list[CitySnapshot] = []
    all_monthly_deals: dict[str, dict[str, float]] = {}

    for city, code in CITY_DOMAIN.items():
        snap, monthly = collect_city_data(city, code)
        snapshots.append(snap)
        all_monthly_deals[city] = monthly

    append_snapshots(snapshot_file, snapshots, run_date)
    history = load_listing_history(snapshot_file)
    render_html(all_monthly_deals, history, output_html)
    write_latest_json(snapshots, all_monthly_deals, history, output_json)
    return {"snapshots": snapshots, "output_html": str(output_html), "output_json": str(output_json)}


def main() -> None:
    p = argparse.ArgumentParser(description="抓取重点城市二手房数据")
    p.add_argument("--snapshot-file", default="data/snapshots.csv")
    p.add_argument("--output-html", default="output/house_trends.html")
    p.add_argument("--output-json", default="output/latest.json")
    args = p.parse_args()

    result = run(Path(args.snapshot_file), Path(args.output_html), Path(args.output_json))
    print("采集结果（最新快照）")
    for s in result["snapshots"]:
        print(f"- {s.city}: 挂牌量={s.listing_count}, 最近月成交均价={s.recent_deal_avg_price}")
    print(f"\nHTML 图表输出: {result['output_html']}")
    print(f"JSON 数据输出: {result['output_json']}")


if __name__ == "__main__":
    main()
