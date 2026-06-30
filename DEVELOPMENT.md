# 开发指南

1. 从 `main` 创建功能分支。
2. 不直接修改生产数据库；先运行 `scripts/backup_runtime.py`。
3. 行情源变更只修改 `src/astock_terminal/providers/`，保持 `Quote` 数据结构不变。
4. API变更同步更新 `docs/API.md` 和前端 `static/app.js`。
5. 新功能必须增加测试，禁止以模拟行情替代失败数据。
6. 提交前运行 `python scripts/preflight.py`。

## 兼容性

- Python 3.10+；
- 基础版无第三方依赖；
- mootdx固定兼容组合见 `requirements-market.txt`；
- `config.json` 和 `data/terminal.db` 属于运行数据，不应提交真实密钥或用户持仓。
