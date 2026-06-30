# HTTP API

## GET

- `/api/health`：版本、时间和数据库路径。
- `/api/dashboard`：组合、行情、选股、提醒和来源健康。
- `/api/selection`：基金经理、共同增持和股票评分。
- `/api/report`：Markdown日报。

## POST

- `/api/refresh`：刷新行情并重建提醒。
- `/api/watchlist`：更新观察池。
- `/api/positions`：新增或更新持仓。
- `/api/positions/recognize`：从持仓截图OCR结果或手动粘贴文本识别持仓；`apply=true`时写入本地持仓。
- `/api/settings`：更新本地设置。
- `/api/position-size`：仓位测算。
- `/api/news/import`：导入已授权新闻事件。
- `/api/news/refresh`：按 `config.json` 中的 `rss_sources` 和 `json_news_sources` 拉取已配置新闻源。
- `/api/selection/import`：导入已核验的基金和股票评分。
- `/api/notifications/test`：测试通知通道。

## DELETE

- `/api/positions/{symbol}`：删除持仓。

所有新增接口必须返回JSON错误，不得以空列表掩盖超时、限流或解析失败。
