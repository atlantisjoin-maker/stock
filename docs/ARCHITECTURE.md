# 系统架构

```text
浏览器/PWA
  ↓ HTTP JSON
astock_terminal.app.Handler
  ↓
业务编排：行情校验、持仓、选股、新闻、提醒
  ↓
SQLite terminal.db
  ↑
行情适配器：mootdx / 腾讯
官方报告导入 / 授权新闻导入 / 通知Webhook
```

## 修改边界

- 新增行情源：实现与 `providers/tencent.py` 相同的返回合同。
- 新增页面：修改 `static/index.html`、`styles.css`、`app.js`。
- 新增API：在 `Handler` 增加路由，并同步 `docs/API.md`。
- 数据库变更：必须增加幂等迁移，不直接删除旧字段。
- 评分变更：保存模型版本，避免新旧日报无法比较。
