# 发布流程

1. 运行 `python scripts/backup_runtime.py`。
2. 更新 `CHANGELOG.md`。
3. 运行 `python scripts/bump_version.py X.Y.Z`。
4. 运行 `python scripts/preflight.py`。
5. 运行 `python scripts/package_release.py`。
6. 核对 `PROJECT_MANIFEST.json` 和SHA-256。
7. 在干净目录启动运行包并测试首页、持仓、日报和行情失败关闭。
8. 发布时同时提供源码包、运行包、校验文件和变更说明。
