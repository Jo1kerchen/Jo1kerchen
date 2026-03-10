# X/Twitter Metrics Scraper (CLI + Streamlit)

本项目提供两种本地使用方式：
- **CLI**（命令行批量抓取）
- **Streamlit Web UI**（网页粘贴链接并下载 CSV）

抓取字段：
- Likes
- Replies
- Reposts
- Views

> 不使用 X API，不需要 Bearer Token。  
> 默认按未登录状态抓取公开推文页面。

---

## 1) 安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2) 安装 Playwright 浏览器

```bash
python3 -m playwright install chromium
```

---

## CLI 用法

### 单条链接

```bash
python3 x_metrics_browser.py "https://x.com/aaa/status/123"
```

### 多条链接（命令行直接传）

```bash
python3 x_metrics_browser.py \
  "https://x.com/aaa/status/123" \
  "https://x.com/bbb/status/456" \
  --output-csv results.csv
```

### 从文件批量输入

```bash
python3 x_metrics_browser.py --input-file links.txt --output-csv results.csv
```

`links.txt` 示例（自动忽略空行并 trim）：

```text
https://x.com/aaa/status/123
https://x.com/bbb/status/456
https://twitter.com/ccc/status/789
```

### 常用参数

- `--output-csv results.csv`
- `--headless`
- `--timeout 45`
- `--debug-views`

---

## Streamlit 网页用法（新增）

启动：

```bash
streamlit run app.py
```

页面能力：
- 大文本框粘贴多条链接（每行一条）
- 点击 **Start scraping** 开始抓取
- 网页显示抓取进度
- 网页展示结果表格
- 一键 **Download CSV**

---

## CSV 字段说明

CSV 列名：

- `input_url`
- `tweet_id`
- `is_repost`
- `current_likes`
- `current_replies`
- `current_reposts`
- `current_views`
- `original_tweet_id`
- `original_likes`
- `original_replies`
- `original_reposts`
- `original_views`
- `status`（`success` / `failed`）
- `error`

说明：
- 如果不是 repost，`original_*` 字段为空。
- 单条失败不会中断批量，后续继续执行。
- 输出顺序与输入顺序严格一致。

CSV 示例：

```csv
input_url,tweet_id,is_repost,current_likes,current_replies,current_reposts,current_views,original_tweet_id,original_likes,original_replies,original_reposts,original_views,status,error
https://x.com/aaa/status/123,123,No,10,2,3,205,,,,,,success,
https://x.com/bbb/status/456,456,Yes,5,1,1,88,999,100,20,30,5000,success,
https://x.com/bad/url,,,,,,,,,,,,failed,Invalid URL
```

---

## 输出示例（CLI 单条）

```text
Input URL: https://x.com/xxx/status/123
Tweet ID: 123
Is repost: Yes

Current post metrics:
  Tweet ID: 123
  Likes: 10
  Replies: 2
  Reposts: 3
  Views: 205

Original post metrics:
  Tweet ID: 999
  Likes: 100
  Replies: 20
  Reposts: 30
  Views: 5000
```

---

## 兼容性与策略

- 支持 `x.com` 和 `twitter.com` 的 `/status/` 链接
- 默认未登录抓取
- 不使用本机浏览器 profile
- 保留 repost/original post 识别逻辑
- 保留 Views 抓取与 `--debug-views` 调试能力
- 不包含 Quotes 字段
