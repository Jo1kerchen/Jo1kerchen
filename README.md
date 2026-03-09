# 国家统计局官方口径：二手住宅价格趋势 API + 微信小程序

本项目已**完全切换**为国家统计局（NBS）官方公开数据口径，不再使用不稳定的房产网站抓取。

## 使用的数据源

- 发布机构：国家统计局
- 数据口径：**70个大中城市商品住宅销售价格变动情况（重点使用二手住宅指标）**
- 官方入口：<https://www.stats.gov.cn/sj/zxfb/>

> 说明：NBS 70城口径是官方稳定公开口径，适合做趋势监测；但它不是“挂牌量/成交均价”口径。

## 指标说明（新字段）

项目当前返回以下官方指标：

- `mom_index`：环比指数（上月=100）
- `mom_change_pct`：环比涨跌幅（%）= `mom_index - 100`
- `yoy_index`：同比指数（上年同月=100）
- `yoy_change_pct`：同比涨跌幅（%）= `yoy_index - 100`

### 与旧版本区别

旧版本：`listing_count`（挂牌量）、`recent_deal_avg_price`（成交均价）  
新版本：官方口径价格指数与涨跌幅（更稳定、可持续）。

另外，NBS 70城口径不包含香港，因此本版本不返回香港指标。

---

## 1) 本地运行

```bash
python realtime_house_trends.py
```

默认输出：

- `data/snapshots.csv`：每次运行快照（用于趋势历史）
- `output/house_trends.html`：可视化图表
- `output/latest.json`：API/小程序可直接使用的数据

---

## 2) 启动 API 服务

```bash
python api_server.py
```

接口：

- `GET /health`
- `GET /api/latest`
- `POST /api/refresh`
- `GET /house_trends.html`

---

## 3) 微信小程序接入

目录：`wechat-miniprogram/`

1. 修改 `wechat-miniprogram/pages/index/index.js` 中 `API_BASE`
2. 微信公众平台配置 request 合法域名（HTTPS）
3. 微信开发者工具导入并上传

---

## 4) 定时刷新

```bash
0 * * * * curl -X POST https://你的域名/api/refresh
```
