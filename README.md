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
python moutai_secondary_market_chart.py \
  --fetch-stock \
  --fetch-liquor \
  --start 2025-01-01 \
  --end 2026-03-01 \
  --align-mode inner \
  --fill-mode none
```

默认输出：

- 抓取结果（清洗后）
  - `data/stock_prices_auto.csv`
  - `data/liquor_prices_auto.csv`
- 抓取原始记录（用于复核）
  - `data/stock_prices_auto_raw.csv`
  - `data/liquor_prices_auto_raw.csv`
- 对齐后数据
  - `output/moutai_auto_aligned.csv`
- 图表
  - `output/moutai_auto_dual.svg`
  - `output/moutai_auto_normalized.svg`

> 说明：PNG 为本地运行时可选产物，已加入 `.gitignore`，不纳入仓库提交。

### 抓取来源与口径

1) **股票（自动抓取）**
- 来源：Eastmoney K线接口（`secid=1.600519`）
- 字段：日线 OHLCV 等（raw），并提取 `date, close` 用于对齐绘图
- 失败处理：抛出明确错误（如 403、超时、空数据）并停止执行，不静默回退

2) **酒价（自动抓取）**
- 默认来源页面：`https://www.jiuxiwang.cn/`（可用 `--liquor-source-url` 覆盖）
- 商品名称匹配规则：
  - 包含关键词：`茅台`、`26`、`飞天`
  - 排除关键词：`生肖`、`礼盒`、`年份酒`、`整箱`
- 价格口径：默认 `散瓶/单瓶口径`（可用 `--price-caliber` 自定义）
- 同日多报价聚合：**中位数（median）**
- 输出字段：`date, secondary_price, source, product, caliber, aggregation, quote_count`

> 注意：若来源页面为 JS 动态加载、需要登录或触发反爬，可能抓不到记录并报错。详见“限制与失效场景”。

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

- 双轴图用于趋势对比，不代表绝对量级可比。
- 两序列单位不同（元 vs 元，但量级差异大），重点观察拐点和方向，不直接比较绝对涨跌幅。
- 另提供归一化图（首日=100）辅助比较阶段走势。

### 限制与可能失效的数据源场景

以下情况可能导致 403/空结果/解析失败：
- 出网受限（公司代理、云环境防火墙）
- 目标站启用反爬（UA、频率、Cookie、Referer 校验）
- 页面改为前端动态渲染，静态 HTML 不再包含价格/日期
- DOM 结构变更导致正则匹配失败

发生失败时：
- 股票抓取会直接给出错误原因并终止；
- 酒价抓取会提示是“页面不可达/无匹配记录/日期区间为空”等具体原因。
