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
