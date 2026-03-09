# 微信小程序部署说明

1. 打开 `pages/index/index.js`，将 `API_BASE` 改成你的后端域名（例如 `https://house-api.example.com`）。
2. 在微信公众平台 -> 开发管理 -> 开发设置，把该域名加入 **request 合法域名**。
3. 用微信开发者工具导入 `wechat-miniprogram` 目录并上传。
