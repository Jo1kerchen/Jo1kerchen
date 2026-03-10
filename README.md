# X/Twitter Metrics CLI

一个基于 Python 3 的命令行工具，用于从 X/Twitter 链接提取 tweet id，并调用 X API v2 获取互动数据。

## 功能

- 支持解析 `x.com` 和 `twitter.com` 链接
- 自动提取 tweet id
- 读取环境变量 `X_BEARER_TOKEN`
- 调用 X API v2 获取：
  - `like_count`
  - `reply_count`
  - `retweet_count`
  - `quote_count`
- 特别处理 repost/retweet：
  - 先输出当前 repost 这条 post 的数据
  - 再输出原始 tweet 的数据（若能识别到原始 tweet id）

## 安装

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 配置环境变量

```bash
export X_BEARER_TOKEN="你的 Bearer Token"
```

## 使用方式

```bash
python x_metrics.py "https://x.com/username/status/1234567890"
```

也支持：

```bash
python x_metrics.py "https://twitter.com/username/status/1234567890"
```

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
Original post metrics:
  Tweet ID: 999
  Likes: 100
  Replies: 20
  Reposts: 30
  Quotes: 10
```

如果不是 repost，程序只会输出 `Current post metrics`。

## 异常处理

脚本已处理常见错误，包括：

- URL 格式不合法
- 未设置 `X_BEARER_TOKEN`
- 网络请求失败
- API 返回 401/404/其他错误
- API 返回数据缺失或 JSON 无法解析
