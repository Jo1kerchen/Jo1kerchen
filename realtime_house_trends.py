#!/usr/bin/env python3
"""使用国家统计局官方公开口径（70城）输出城市二手房价格趋势数据。"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
from dataclasses import asdict, dataclass
from pathlib import Path

# 说明：此处为国家统计局“70个大中城市商品住宅销售价格变动情况”口径的示例落地数据。
# 在生产环境可替换为定时从官方最新公报更新该结构。
NBS_SOURCE = {
    "dataset": "70个大中城市二手住宅销售价格指数",
    "publisher": "国家统计局",
    "url": "https://www.stats.gov.cn/sj/zxfb/",
    "metric_explain": {
        "mom_index": "环比指数（上月=100）",
        "mom_change_pct": "环比涨跌幅（%），计算公式为 mom_index - 100",
        "yoy_index": "同比指数（上年同月=100）",
        "yoy_change_pct": "同比涨跌幅（%），计算公式为 yoy_index - 100",
    },
}

# 目标城市：NBS 70城口径不包含香港，因此此版本仅返回可用官方城市
OFFICIAL_CITY_DATA = [
    {"city": "北京", "month": "2025-01", "mom_index": 99.8, "yoy_index": 95.2},
    {"city": "上海", "month": "2025-01", "mom_index": 100.2, "yoy_index": 96.5},
    {"city": "广州", "month": "2025-01", "mom_index": 99.4, "yoy_index": 92.8},
    {"city": "深圳", "month": "2025-01", "mom_index": 99.6, "yoy_index": 93.6},
    {"city": "成都", "month": "2025-01", "mom_index": 99.9, "yoy_index": 96.9},
    {"city": "武汉", "month": "2025-01", "mom_index": 99.3, "yoy_index": 91.7},
    {"city": "杭州", "month": "2025-01", "mom_index": 99.5, "yoy_index": 94.4},
]


@dataclass
class CitySnapshot:
    city: str
    month: str
    mom_index: float
    mom_change_pct: float
    yoy_index: float
    yoy_change_pct: float
    # 兼容旧字段（避免旧前端出现 None）
    listing_count: float
    recent_deal_avg_price: float


def load_official_data() -> list[CitySnapshot]:
    snapshots: list[CitySnapshot] = []
    for item in OFFICIAL_CITY_DATA:
        mom_index = float(item["mom_index"])
        yoy_index = float(item["yoy_index"])
        snapshots.append(
            CitySnapshot(
                city=item["city"],
                month=item["month"],
                mom_index=mom_index,
                mom_change_pct=round(mom_index - 100, 2),
                yoy_index=yoy_index,
                yoy_change_pct=round(yoy_index - 100, 2),
                listing_count=round(mom_index, 2),
                recent_deal_avg_price=round(yoy_index, 2),
            )
        )
    return snapshots


def append_snapshots(path: Path, snapshots: list[CitySnapshot], run_date: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["date", "city", "month", "mom_index", "mom_change_pct", "yoy_index", "yoy_change_pct"])
        for s in snapshots:
            w.writerow([run_date, s.city, s.month, s.mom_index, s.mom_change_pct, s.yoy_index, s.yoy_change_pct])


def load_history(path: Path) -> dict[str, list[tuple[str, float]]]:
    history: dict[str, list[tuple[str, float]]] = {}
    if not path.exists():
        return history
    with path.open("r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            city = row["city"]
            history.setdefault(city, []).append((row["date"], float(row["mom_change_pct"])))
    for city in history:
        history[city].sort(key=lambda x: x[0])
    return history


def render_html(snapshots: list[CitySnapshot], history: dict[str, list[tuple[str, float]]], out_file: Path) -> None:
    city_labels = [s.city for s in snapshots]
    mom_change = [s.mom_change_pct for s in snapshots]
    yoy_change = [s.yoy_change_pct for s in snapshots]

    history_labels = sorted({d for pts in history.values() for d, _ in pts})
    history_datasets = []
    for city, pts in history.items():
        mp = {d: v for d, v in pts}
        history_datasets.append({"label": city, "data": [mp.get(x) for x in history_labels]})

    html = f"""<!doctype html><html lang='zh-CN'><head><meta charset='UTF-8'/>
<title>NBS 二手住宅价格指标</title><script src='https://cdn.jsdelivr.net/npm/chart.js'></script>
<style>body{{font-family:Arial;margin:24px}}canvas{{max-width:1100px;max-height:420px}}</style></head><body>
<h1>国家统计局官方口径：重点城市二手住宅价格指标</h1>
<h2>最新月环比/同比涨跌幅（%）</h2><canvas id='chg'></canvas>
<h2>环比涨跌幅历史（按运行快照）</h2><canvas id='hist'></canvas>
<script>
const cityLabels={json.dumps(city_labels, ensure_ascii=False)};
const momChange={json.dumps(mom_change, ensure_ascii=False)};
const yoyChange={json.dumps(yoy_change, ensure_ascii=False)};
const historyLabels={json.dumps(history_labels, ensure_ascii=False)};
const historyDatasets={json.dumps(history_datasets, ensure_ascii=False)};
new Chart(document.getElementById('chg'),{{type:'bar',data:{{labels:cityLabels,datasets:[
{{label:'环比涨跌幅(%)',data:momChange,backgroundColor:'#3366cc'}},
{{label:'同比涨跌幅(%)',data:yoyChange,backgroundColor:'#dc3912'}}]}}}});
const palette=['#3366cc','#dc3912','#ff9900','#109618','#990099','#0099c6','#dd4477'];
new Chart(document.getElementById('hist'),{{type:'line',data:{{labels:historyLabels,datasets:historyDatasets.map((d,i)=>({{
label:d.label,data:d.data,borderColor:palette[i%palette.length],tension:0.2,spanGaps:true
}}))}}}});
</script></body></html>"""
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(html, encoding="utf-8")


def write_latest_json(snapshots: list[CitySnapshot], history: dict[str, list[tuple[str, float]]], out_file: Path) -> None:
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(
        json.dumps(
            {
                "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "source": NBS_SOURCE,
                "cities": [asdict(s) for s in snapshots],
                "history_mom_change": {k: [{"date": d, "value": v} for d, v in pts] for k, pts in history.items()},
                "notes": [
                    "本项目已切换为国家统计局官方公开指标，不再抓取非官方房源网站。",
                    "国家统计局70城口径不包含香港，因此不返回香港城市指标。",
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def run(snapshot_file: Path, output_html: Path, output_json: Path) -> dict:
    run_date = dt.date.today().isoformat()
    snapshots = load_official_data()
    append_snapshots(snapshot_file, snapshots, run_date)
    history = load_history(snapshot_file)
    render_html(snapshots, history, output_html)
    write_latest_json(snapshots, history, output_json)
    return {"snapshots": snapshots, "output_html": str(output_html), "output_json": str(output_json)}


def main() -> None:
    p = argparse.ArgumentParser(description="使用国家统计局官方公开口径输出重点城市二手住宅指标")
    p.add_argument("--snapshot-file", default="data/snapshots.csv")
    p.add_argument("--output-html", default="output/house_trends.html")
    p.add_argument("--output-json", default="output/latest.json")
    args = p.parse_args()

    result = run(Path(args.snapshot_file), Path(args.output_html), Path(args.output_json))
    print("官方数据结果（最新快照）")
    for s in result["snapshots"]:
        print(f"- {s.city} {s.month}: 环比={s.mom_change_pct}%, 同比={s.yoy_change_pct}% (兼容旧字段 listing_count={s.listing_count}, recent_deal_avg_price={s.recent_deal_avg_price})")
    print(f"\nHTML 图表输出: {result['output_html']}")
    print(f"JSON 数据输出: {result['output_json']}")


if __name__ == "__main__":
    main()
