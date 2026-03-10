# X/Twitter Metrics Browser CLI

本工具是一个 **本地 Python + Playwright 命令行抓取器**，不使用 X API，不需要 Bearer Token。
输入推文链接后，直接从页面读取互动数据。

## 支持能力

- 输入 `x.com` 或 `twitter.com` 的推文链接，自动提取 Tweet ID
- 抓取并输出：
  - Likes
  - Replies
  - Reposts
  - Quotes（页面可见时）
  - Views（页面可见时）
- 支持 repost/retweet 场景：
  - 先输出 current post metrics
  - 再尝试识别 original post 并输出 original post metrics
- 支持浏览器登录态持久化（第一次手动登录，后续复用）
- 提供更稳健异常处理（加载失败、未登录、元素缺失、超时等）
- 包含调试日志输出

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

> 以上步骤通常仅需首次执行。

---

## 运行方式

### 基础运行

```bash
python3 x_metrics_browser.py "https://x.com/xxx/status/123"
```

### 指定持久化登录目录

```bash
python3 x_metrics_browser.py "https://x.com/xxx/status/123" --user-data-dir .x_browser_profile
```

### 无头模式（仅显式传入才启用）

```bash
python3 x_metrics_browser.py "https://x.com/xxx/status/123" --user-data-dir .x_browser_profile --headless
```

### 调试模式（抓取结束后不立即关闭）

```bash
python3 x_metrics_browser.py "https://x.com/xxx/status/123" --keep-open
```

---

## 首次登录如何操作

1. 默认非 headless，会弹出真实浏览器窗口。
2. 如果程序检测到未登录，会提示：
   - 请在浏览器中手动登录 X/Twitter
   - 登录完成后在终端按回车继续
3. 登录状态会保存到 `--user-data-dir`（默认 `.x_browser_profile`）。
4. 下次运行会优先复用该目录中的登录态。

---

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

如果不是 repost，只输出 `Current post metrics`。
如果无法识别 original post ID，会输出提示，但程序不会崩溃。

---

## Views 与数字标准化

- 支持读取页面可见的浏览量（Views）
- 若页面没有可见 View，输出 `N/A`
- 支持 `K/M/B` 缩写的标准化：
  - `1.2K -> 1200`
  - `3.4M -> 3400000`
  - `2B -> 2000000000`

---

## 稳定性策略

为提升可用性，脚本会：

- 同时尝试 `data-testid`、`aria-label`、可见文本多种方式提取指标
- 兼容中英文常见关键词
- 增加页面渲染等待与重试
- repost 检测失败时给出提示而不是直接退出
- 输出关键日志：
  - 正在启动浏览器
  - 正在打开页面
  - 检测登录状态
  - 正在提取 current/original metrics
  - 抓取成功或失败原因

---

## 常见问题

1. **浏览器一闪而过**
   - 使用 `--keep-open` 保持窗口，或查看失败时的自动延时关闭。

2. **依赖安装失败**
   - 检查网络/代理配置后重试：
     - `pip install -r requirements.txt`
     - `python3 -m playwright install chromium`

3. **无法抓到某项数据（例如 Views）**
   - X 页面可能因区域、账号权限、语言、界面 AB 实验而隐藏该字段，此时会返回 `N/A`。
