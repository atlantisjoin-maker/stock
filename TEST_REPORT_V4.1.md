# V4.1.0 开发维护包测试报告

## 结果

- Python测试环境：`3.13.5`
- Python静态编译：通过
- 自动化测试：**16/16通过**
- HTTP冒烟测试：首页、健康检查、Dashboard均通过
- 预检报告：`ok=true`
- 运行数据库：未打入源码包
- 行情适配器：腾讯与mootdx已拆分为独立模块
- 兼容启动：`python web_app.py`与`python -m astock_terminal`均保留

## 覆盖的关键逻辑

1. 沪、深、北交所代码映射，包含920北交所代码；
2. 官方新闻单源确认与独立媒体多源确认；
3. 行情阻断时可执行新增金额为0；
4. 有效行情和新闻条件下仓位计算；
5. 空数据库不产生虚构基金经理或股票；
6. 标准源码目录、版本文件和行情适配器存在性；
7. HTTP首页、健康检查与Dashboard接口。

## 测试输出

```text
[2026-06-29T21:39:13+08:00] 127.0.0.1 "GET /api/dashboard HTTP/1.1" 200 -
[2026-06-29T21:39:13+08:00] 127.0.0.1 "GET /api/health HTTP/1.1" 200 -
[2026-06-29T21:39:13+08:00] 127.0.0.1 "GET / HTTP/1.1" 200 -
test_database_empty_no_fake_selection (test_core.CoreTests.test_database_empty_no_fake_selection) ... ok
test_event_official (test_core.CoreTests.test_event_official) ... ok
test_event_single_media (test_core.CoreTests.test_event_single_media) ... ok
test_event_two_media (test_core.CoreTests.test_event_two_media) ... ok
test_exchange_920 (test_core.CoreTests.test_exchange_920) ... ok
test_exchange_sh (test_core.CoreTests.test_exchange_sh) ... ok
test_normalize (test_core.CoreTests.test_normalize) ... ok
test_position_allowed (test_core.CoreTests.test_position_allowed) ... ok
test_position_block_quote (test_core.CoreTests.test_position_block_quote) ... ok
test_static_exists (test_core.CoreTests.test_static_exists) ... ok
test_no_runtime_db_in_source (test_package_layout.PackageLayoutTests.test_no_runtime_db_in_source) ... ok
test_provider_modules (test_package_layout.PackageLayoutTests.test_provider_modules) ... ok
test_version_files (test_package_layout.PackageLayoutTests.test_version_files) ... ok
test_dashboard (test_server.ServerTest.test_dashboard) ... ok
test_health (test_server.ServerTest.test_health) ... ok
test_index (test_server.ServerTest.test_index) ... ok

----------------------------------------------------------------------
Ran 16 tests in 0.640s

OK

```

## 未在当前环境执行

- mootdx真实服务器联网稳定性；
- 腾讯行情实时字段长期契约测试；
- 社交通知真实Webhook发送；
- 官方基金季度报告在线同步。

上述项目需要在部署网络和合法授权环境中执行验收；系统在数据不可用时保持失败关闭。
