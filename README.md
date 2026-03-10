# Telegram 公开消息浏览量抓取工具

这是一个独立的 Telegram 公共消息浏览量抓取网页工具，技术栈为 **Python + Playwright + Streamlit**。

## 功能

- 支持在网页文本框中多行粘贴 Telegram 公开消息链接（每行一个）
- 按输入顺序批量抓取每条消息的 views
- 单条抓取失败不会中断后续任务
- 表格展示抓取结果
- 一键下载 CSV

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

启动后在浏览器中打开 Streamlit 页面，粘贴如下格式链接即可抓取：

```text
https://t.me/channelname/123
```

> 当前版本仅支持公开 Telegram 消息链接。
