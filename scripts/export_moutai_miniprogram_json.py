from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path


def ema(values: list[float], span: int) -> list[float]:
    alpha = 2 / (span + 1)
    out = []
    prev = None
    for v in values:
        prev = v if prev is None else (alpha * v + (1 - alpha) * prev)
        out.append(round(prev, 4))
    return out


def max_runup_and_drawdown(closes: list[float]) -> tuple[float, float]:
    min_v = closes[0]
    max_up = -1.0
    run_max = closes[0]
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


def main() -> None:
    in_csv = Path('output/moutai_stock_vs_liquor_merged.csv')
    out_json = Path('wechat-miniprogram/data/moutai_stock_vs_liquor.json')
    if not in_csv.exists():
        raise FileNotFoundError(f'Missing {in_csv}, run moutai_secondary_market_chart.py first')

    rows = []
    with in_csv.open('r', encoding='utf-8') as f:
        r = csv.DictReader(f)
        for row in r:
            if not row.get('bulk_price'):
                continue
            rows.append({
                'date': row['date'],
                'close': float(row['close']),
                'bulk_price': float(row['bulk_price']),
                'original_box_price': float(row['original_box_price']) if row.get('original_box_price') else None
            })

    if not rows:
        raise RuntimeError('No valid rows in merged csv')

    closes = [x['close'] for x in rows]
    liquors = [x['bulk_price'] for x in rows]
    e20 = ema(closes, 20)
    e55 = ema(closes, 55)
    e100 = ema(closes, 100)
    e200 = ema(closes, 200)
    max_up, max_dd = max_runup_and_drawdown(closes)

    for i, row in enumerate(rows):
        row['ema20'] = e20[i]
        row['ema55'] = e55[i]
        row['ema100'] = e100[i]
        row['ema200'] = e200[i]

    payload = {
        'meta': {
            'title': '贵州茅台股价与飞天茅台53度散瓶市场参考价走势对比',
            'subtitle': '时间范围：2018-01-01 至今（按A股交易日对齐）',
            'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        },
        'summary': {
            'max_runup_pct': round(max_up * 100, 2),
            'max_drawdown_pct': round(max_dd * 100, 2),
            'latest_close': round(closes[-1], 2),
            'latest_liquor': round(liquors[-1], 2),
            'latest_date': rows[-1]['date']
        },
        'data': rows
    }

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Wrote {out_json}')


if __name__ == '__main__':
    main()
