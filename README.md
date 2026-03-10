# X/Twitter Metrics Browser CLI

一个本地 Python 工具：不使用 X API、不需要 Bearer Token，直接用 Playwright 打开帖子页面并读取互动数据。

## 功能

- 输入 `x.com` / `twitter.com` 帖子链接，自动提取 tweet ID
- 使用 Playwright 打开页面并抓取：
  - Likes
  - Replies
  - Reposts
  - Quotes（页面可见时）
  - Views（页面可见时）
- 尝试识别 repost/retweet：
  - 先输出当前链接页面（current post）的数据
  - 若能识别原帖 ID，再输出 original post 的数据
- 首次运行支持手动登录，后续复用本地浏览器登录状态
- 兼容常见错误场景：页面加载失败、元素缺失、未登录、超时等

## 环境要求

- Python 3.9+
- Playwright

## 安装

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

> `playwright install chromium` 只需要执行一次（或浏览器缺失时执行）。

## 使用方式

```bash
python x_metrics_browser.py "https://x.com/xxx/status/123"
```

也支持：

```bash
python x_metrics_browser.py "https://twitter.com/xxx/status/123"
```

### 可选参数

```bash
python x_metrics_browser.py "<url>" \
  --user-data-dir .x_browser_profile \
  --timeout 30 \
  --headless
```

- `--user-data-dir`：浏览器持久化目录（默认 `.x_browser_profile`）
- `--timeout`：单次页面加载超时时间（秒）
- `--headless`：无头模式（首次登录不推荐）

## 首次登录说明

第一次运行时，如果检测到登录页，程序会提示你在浏览器中手动登录，然后在终端按回车继续。
登录状态会保存在 `--user-data-dir` 指定目录中，后续会自动复用。

## 输出示例

```text
Input URL: https://x.com/xxx/status/123
Tweet ID: 123
Is repost: Yes
Current post metrics:
  Tweet ID: 123
  Likes: 10
  Replies: 2
  Reposts: 3
  Quotes: 1
  Views: 205
Original post metrics:
  Tweet ID: 999
  Likes: 100
  Replies: 20
  Reposts: 30
  Quotes: 10
  Views: 5000
```

> 如果不是 repost，则只输出 `Current post metrics`。
> 如果是 repost 但无法识别原帖 ID，会给出提示。

## 异常处理

脚本会处理并输出易懂错误信息，包括：

- 链接格式不合法
- 页面加载超时/失败
- 未找到帖子元素
- 登录未完成导致无法读取页面
- Playwright 运行异常
