# Telegram 公开消息浏览量抓取工具

这是一个独立的 Telegram 公共消息浏览量抓取网页工具，技术栈为 **Python + Playwright + Streamlit**。

## 功能

- 支持在网页文本框中多行粘贴 Telegram 公开消息链接（每行一个）
- 按输入顺序批量抓取每条消息的 views
- 单条抓取失败不会中断后续任务
- 表格展示抓取结果
- 一键下载 CSV
- 支持 Telegram 外层壳 + widget/embed + iframe 结构抓取
- 支持 Debug 模式（页面结构调试），包含主页面与 frame 的完整信息：
  - 原始 URL、最终 URL、页面标题
  - 页面可见文本/`body.innerText` 片段
  - 关键词命中文本节点
  - 消息容器候选节点文本（含 `data-telegram-post` / iframe src）
  - frame 数量、每个 frame 的 URL / title / 前 1000 字符文本
  - 命中消息候选 frame 与命中 views 的 frame

输出字段包括：

- `input_url`
- `channel_name`
- `message_id`
- `views`
- `status`
- `error`

## 项目结构

```text
telegram_views_tool/
  app.py
requirements.txt
README.md
```

## 安装依赖

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

## 启动方式

```bash
cd telegram_views_tool
streamlit run app.py
```

可选：通过 CLI 参数打开默认 Debug 和默认超时（秒）：

```bash
streamlit run app.py -- --debug --timeout 30
```

启动后在浏览器中打开 Streamlit 页面，粘贴如下格式链接即可抓取：

```text
https://t.me/channelname/123
```

> 当前版本仅支持公开 Telegram 消息链接。
