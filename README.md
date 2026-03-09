# 国家统计局官方口径：二手住宅价格趋势 API + 微信小程序

本项目已切换为国家统计局（NBS）官方公开口径，并解决“运行后还是 `None`”的问题：

- 优先尝试读取国家统计局官网数据
- 若官网访问受限/反爬，自动回退到官方口径基线数据
- 输出中保留旧字段兼容映射，避免旧页面出现 `None`

## 数据源与字段

- 发布机构：国家统计局
- 口径：70个大中城市二手住宅价格指数
- 官方入口：<https://www.stats.gov.cn/sj/zxfb/>

返回字段：

- `mom_index`：环比指数（上月=100）
- `mom_change_pct`：环比涨跌幅（%）=`mom_index - 100`
- `yoy_index`：同比指数（上年同月=100）
- `yoy_change_pct`：同比涨跌幅（%）=`yoy_index - 100`

兼容旧字段（仅为兼容旧前端）：

- `listing_count` -> 映射 `mom_index`
- `recent_deal_avg_price` -> 映射 `yoy_index`

> NBS 70城口径不包含香港，因此不输出香港指标。

## 运行

```bash
python realtime_house_trends.py
```

输出包括：

- `data/snapshots.csv`
- `output/house_trends.html`
- `output/latest.json`

并在控制台显示 `source_mode`：

- `nbs_website`：成功从官网抓取
- `local_official_file`：使用本地 `nbs_official_data.json`
- `official_fallback`：官网不可用时使用内置官方口径基线数据（不会返回 None）

## 可选：手工更新官方数据

你可以在项目根目录创建 `nbs_official_data.json`，格式如下：

```json
[
  {"city": "北京", "month": "2025-02", "mom_index": 99.7, "yoy_index": 95.1}
]
```

脚本会优先读取这个文件。

## API

```bash
python api_server.py
```

- `GET /health`
- `GET /api/latest`
- `POST /api/refresh`
- `GET /house_trends.html`

## 微信小程序

目录：`wechat-miniprogram/`

1. 修改 `wechat-miniprogram/pages/index/index.js` 的 `API_BASE`
2. 配置 request 合法域名（HTTPS）
3. 上传发布
