# 二手房趋势采集 + 微信小程序部署

这个项目已经包含两部分：

1. **Python 采集器**：实时抓取北京、上海、广州、深圳、成都、武汉、杭州、香港的二手房挂牌量与成交价格趋势。
2. **可部署 API 服务 + 微信小程序示例**：小程序直接请求 API 展示实时数据。

---

## 1) 本地运行（先跑通）

```bash
python realtime_house_trends.py
```

默认会生成：

- `data/snapshots.csv`：每次采集快照（用于挂牌量趋势累积）
- `output/house_trends.html`：网页走势图
- `output/latest.json`：给小程序/前端调用的结构化数据

可选参数：

```bash
python realtime_house_trends.py \
  --snapshot-file data/snapshots.csv \
  --output-html output/house_trends.html \
  --output-json output/latest.json
```

---

## 2) 启动后端 API（给微信小程序调用）

```bash
python api_server.py
```

默认监听 `8000` 端口，提供：

- `GET /health`：健康检查
- `GET /api/latest`：返回最新采集数据（JSON）
- `POST /api/refresh`：触发一次实时抓取并刷新数据
- `GET /house_trends.html`：网页走势图

环境变量（可选）：

- `HOST`（默认 `0.0.0.0`）
- `PORT`（默认 `8000`）
- `SNAPSHOT_FILE`
- `OUTPUT_HTML`
- `OUTPUT_JSON`

---

## 3) 部署到服务器（推荐）

以 Ubuntu 为例：

```bash
# 1) 上传项目代码到服务器
# 2) 启动服务
cd /path/to/project
nohup python api_server.py > server.log 2>&1 &

# 3) 验证
curl http://127.0.0.1:8000/health
```

然后将 `https://你的域名` 反向代理到该服务（Nginx/Caddy 均可），得到公网 HTTPS 地址。

> 微信小程序要求 HTTPS 且域名需备案并加入合法域名。

---

## 4) 微信小程序接入

小程序示例目录：`wechat-miniprogram/`

操作：

1. 打开 `wechat-miniprogram/pages/index/index.js`，把：
   - `const API_BASE = 'https://YOUR_DOMAIN';`
   改成你的真实 API 域名。  
2. 到微信公众平台将该域名加入 `request 合法域名`。  
3. 用微信开发者工具导入 `wechat-miniprogram` 目录，预览并上传。

---

## 5) 定时“实时”采集建议

可以用 `crontab` 每小时刷新一次：

```bash
0 * * * * curl -X POST https://你的域名/api/refresh
```

这样小程序打开就能读到最新数据。

---

## 注意事项

- 若运行环境访问目标站点受限（403/反爬），抓取结果可能为 `None`，接口仍可正常返回。
- 需要更强稳定性时，建议增加代理池、重试、以及可授权数据源。

---
---

## 6) 贵州茅台（26年飞天）二级市场价 + 600519 A股：全自动抓取与出图

### 一键全流程

```bash
python moutai_secondary_market_chart.py   --fetch-stock   --fetch-liquor   --history-index-url "https://mp.weixin.qq.com/mp/appmsgalbum?..."   --start 2025-01-01   --end 2026-03-01   --align-mode inner   --fill-mode none
```

默认输出：

- 抓取结果（清洗后）
  - `data/stock_prices_auto.csv`
  - `data/liquor_prices_auto.csv`
- 抓取原始记录（用于复核）
  - `data/stock_prices_auto_raw.csv`
  - `data/liquor_prices_auto_raw.csv`
- **公众号历史文章索引清单**
  - `data/liquor_article_index.csv`
- 对齐后数据
  - `output/moutai_auto_aligned.csv`
- 图表（入库保留 SVG）
  - `output/moutai_auto_dual.svg`
  - `output/moutai_auto_normalized.svg`

> 说明：PNG 为本地运行时可选产物，已加入 `.gitignore`，不纳入仓库提交。

### 抓取主入口（已改为历史列表页）

酒价抓取不再以单篇文章页为入口，改为：

- **主入口**：公众号历史文章列表页 / 相册索引页（`--history-index-url`）
- 先提取文章索引（标题、日期、文章 URL 或跳转参数）
- 再批量抓取正文并解析价格

脚本会优先尝试从列表页响应中提取：
1. 文章标题
2. 文章日期
3. 每篇文章 URL（若缺失则尝试保留 `__biz/mid/idx/sn` 跳转参数）

若索引页无法提取 URL/参数，脚本会给出明确报错并说明缺失项，不要求手工逐篇点开。

### 抓取来源与口径

1) **股票（自动抓取）**
- 来源：Eastmoney K线接口（`secid=1.600519`）
- 字段：日线 OHLCV 等（raw），并提取 `date, close` 用于对齐绘图
- 失败处理：抛出明确错误（如 403、超时、空数据）并停止执行

2) **酒价（自动抓取）**
- 来源：公众号历史文章列表页（如你提供的相册索引链接）
- 商品匹配规则：
  - 标题过滤：包含 `茅台`、`26`、`飞天`
  - 排除：`生肖`、`礼盒`、`年份酒`、`整箱`
- 价格口径：默认 `散瓶/单瓶口径`（`--price-caliber` 可改）
- 同日多报价聚合：**中位数（median）**
- 输出字段：`date, secondary_price, source, product, caliber, aggregation, quote_count`

### 数据清洗与对齐规则

- 去重：同日重复记录按中位数聚合
- 空值：空值不参与聚合
- 异常值提示：IQR 法（1.5×IQR）仅告警，不自动删除
- 日期对齐（`--align-mode`）
  - `inner`：仅保留双方都存在的日期
  - `left`：保留酒价日期为主轴，股票可为空
- 缺失值处理（`--fill-mode`）
  - `none`：不填充，最终绘图前丢弃任一侧为空的日期
  - `ffill`：按时间前向填充缺失值

### 图表说明

- 双轴图仅用于趋势对比，不代表绝对量级可比。
- 两序列单位不同，重点观察拐点与方向，不直接比较绝对涨跌幅。
- 另提供归一化图（首日=100）辅助比较阶段走势。

### 限制与失效场景

以下情况可能导致索引解析或正文抓取失败：
- 出网受限（代理/防火墙）导致 403
- 列表页为前端动态加载，首包不含文章清单
- 列表响应不含可用 URL，且缺少可拼接 URL 的参数（`__biz/mid/idx/sn`）
- 正文价格为图片/表格，文本正则难以直接提取

失败时脚本会明确提示：是“索引页不可访问 / 索引无标题日期链接 / 缺URL参数 / 正文无可解析价格”中的哪一类。
