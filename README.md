# X/Twitter Metrics Browser CLI

本工具是本地运行的 Python 命令行工具，使用 **Playwright 默认浏览器环境** 抓取推文页面指标：
- Likes
- Replies
- Reposts
- Quotes
- Views

> 不使用 X API，不需要 Bearer Token。  
> 默认按未登录状态抓取（公开推文通常可直接读取部分数据）。

## 功能说明

- 输入 `x.com` 或 `twitter.com` 的帖子链接，自动提取 Tweet ID
- 默认不要求登录，优先直接抓取页面可见数据
- 若页面有登录/注册弹窗，脚本会先尝试关闭或绕过
- 支持 repost/retweet：
  - 先输出 current post metrics
  - 再尝试抓取 original post metrics
- 支持 Views 抓取
- 对 `K/M/B` 缩写数字做标准化：
  - `1.2K -> 1200`
  - `3.4M -> 3400000`
  - `2B -> 2000000000`
- 支持中英文界面关键字提取

## 安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 安装 Playwright 浏览器

```bash
python3 -m playwright install chromium
```

## 运行方式

默认非 headless（方便调试）：

```bash
python3 x_metrics_browser.py "https://x.com/xxx/status/123"
```

也支持 twitter.com：

```bash
python3 x_metrics_browser.py "https://twitter.com/xxx/status/123"
```

使用无头模式：

```bash
python3 x_metrics_browser.py "https://x.com/xxx/status/123" --headless
```

可调超时（秒）：

```bash
python3 x_metrics_browser.py "https://x.com/xxx/status/123" --timeout 45
```

## 登录策略（默认不强制登录）

- 程序默认尝试在未登录状态下抓取。
- 如果页面在未登录状态可读到数据，会直接输出，不会要求登录。
- 只有当页面确实无法稳定提取时，程序会提示：
  - `当前页面在未登录状态下无法稳定提取数据`

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

如果不是 repost，则只输出 `Current post metrics`。  
如果 original post 无法识别或访问，会输出提示但程序不崩溃。

## 调试日志

脚本会输出适量日志，例如：
- `Launching browser...`
- `Opening page...`
- `Attempting extraction without login...`
- `Page loaded`
- `Extracting current post metrics...`
- `Extracting original post metrics...`
- `Extraction succeeded / failed`

## 常见失败原因

- 推文不可见（删除、私密、地区限制）
- 页面动态加载未完成
- 未登录状态下弹层遮挡且无法自动关闭

遇到失败可尝试：
- 增大 `--timeout`
- 使用非 headless 模式观察页面真实状态
