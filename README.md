# X/Twitter Metrics Browser CLI

本工具是本地运行的 Python 命令行工具，使用 **Playwright 默认浏览器环境** 抓取推文页面指标：
- Likes
- Replies
- Reposts
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
- 支持 Views 抓取（重点增强）
- 对 `K/M/B` 缩写数字做标准化：
  - `1.2K -> 1200`
  - `3.4M -> 3400000`
  - `2B -> 2000000000`
- 支持中文“万”格式标准化：
  - `2万次查看 -> 20000`
- 支持中英文界面关键字提取
- 支持批量链接抓取并导出 CSV（按输入顺序处理）

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

### 单条链接（保留）

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

`links.txt` 示例（自动跳过空行，自动去除首尾空格）：

```text
https://x.com/aaa/status/123
https://x.com/bbb/status/456
https://twitter.com/ccc/status/789
```

### 可选参数

- `--headless`：无头模式
- `--timeout 45`：单页超时秒数
- `--debug-views`：打印 Views 候选节点调试信息
- `--output-csv results.csv`：将结果写入 CSV

## Views 专项调试

当你怀疑 Views 一直是 `N/A` 时，可打开调试模式：

```bash
python3 x_metrics_browser.py "https://x.com/xxx/status/123" --debug-views
```

调试模式会额外打印：
- 包含 `view/views/浏览/查看/次查看/impression(s)` 的节点（text / aria-label / title / data-testid）
- article 附近可能的 metric 节点原始文本
- metric row 子节点按顺序的原始文本

## CSV 输出字段

CSV 列名如下：

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
- `error`（失败原因；成功为空）

说明：
- 非 repost 时，`original_*` 字段为空。
- 单条失败不会中断批量任务，后续链接继续处理。
- CSV 顺序与输入顺序严格一致。

## CSV 示例

```csv
input_url,tweet_id,is_repost,current_likes,current_replies,current_reposts,current_views,original_tweet_id,original_likes,original_replies,original_reposts,original_views,status,error
https://x.com/aaa/status/123,123,No,10,2,3,205,,,,, ,success,
https://x.com/bbb/status/456,456,Yes,5,1,1,88,999,100,20,30,5000,success,
https://x.com/bad/url,,, , , , , , , , , ,failed,Invalid URL
```

## 单条输出示例

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

如果不是 repost，则只输出 `Current post metrics`。  
如果 original post 无法识别或访问，会输出提示但程序不崩溃。

## 运行日志

批量模式会输出简洁进度，例如：

```text
[1/10] Processing: https://x.com/aaa/status/123
[2/10] Processing: https://x.com/bbb/status/456
```

## 常见失败原因

- 推文不可见（删除、私密、地区限制）
- 页面动态加载未完成
- 未登录状态下弹层遮挡且无法自动关闭
- 当前页面结构变化导致 Views 位置变化（建议使用 `--debug-views`）

遇到失败可尝试：
- 增大 `--timeout`
- 使用非 headless 模式观察页面真实状态
- 加 `--debug-views` 输出候选节点并定位
