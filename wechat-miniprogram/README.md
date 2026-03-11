# 微信小程序部署说明（含茅台双轴原生图）

## 一、页面说明

当前小程序包含两个页面：

1. `pages/index/index`：原有房产数据页面
2. `pages/chart/index`：**原生 Canvas 双轴图页面（非 WebView）**
   - 左轴：贵州茅台股价
   - 右轴：飞天茅台53度散瓶参考价
   - 支持 EMA20/55/100/200 图例开关
   - 支持触摸 tooltip（日期精确到日）
   - 右上角显示：最大涨幅、最大回撤

## 二、准备图表数据（CSV -> JSON）

先在项目根目录运行：

```bash
python3 moutai_secondary_market_chart.py \
  --start 2018-01-01 \
  --end 2026-12-31 \
  --stock-csv data/moutai_a_share_daily.csv \
  --liquor-csv data/moutai_26_secondary_market_daily.csv \
  --output-merged-csv output/moutai_stock_vs_liquor_merged.csv

python3 scripts/export_moutai_miniprogram_json.py
```

会生成：

- `wechat-miniprogram/data/moutai_stock_vs_liquor.json`

小程序图表页直接读取该 JSON。

## 三、微信开发者工具运行

1. 用微信开发者工具导入 `wechat-miniprogram/` 目录。
2. 编译后进入首页，点击“查看茅台双轴图”。
3. 在图表页可：
   - 触摸查看日期/股价/酒价
   - 点击 EMA 标签开关 EMA20/55/100/200

## 四、接口域名（仅首页房产数据需要）

若使用首页房产 API，请修改 `pages/index/index.js` 中 `API_BASE`。
