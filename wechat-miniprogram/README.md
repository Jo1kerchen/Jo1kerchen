# 微信小程序部署说明（国家统计局官方指标）

1. 打开 `pages/index/index.js`，将 `API_BASE` 改成你的后端域名（如 `https://house-api.example.com`）。
2. 在微信公众平台 -> 开发管理 -> 开发设置，把该域名加入 **request 合法域名**。
3. 导入 `wechat-miniprogram` 目录并上传。
4. 页面显示的是国家统计局 70城口径的二手住宅环比/同比指标，不再是挂牌量/成交均价。
