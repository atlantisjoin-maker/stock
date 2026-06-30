# A股智能投研网页版 V4.1.0

面向本地投研与持仓跟踪的网页版工具。基础运行仅依赖 Python 标准库；`mootdx`、OCR 识别为可选能力。

## 优化后的实时数据方案

- 行情主链路：腾讯财经 HTTP API，负责实时价、涨跌幅、成交额、换手率、PE/PB、市值等字段。
- 行情备用链路：`mootdx` 通达信，可选安装后用于 K 线、盘口和行情交叉验证。
- 新闻链路：接入 `a-stock-data` 方案中的东方财富全球资讯与个股新闻接口，后端统一限流、重试、入库。
- 市场信号链路：接入东方财富人气榜，写入 `stock_scores` 并标记为 `A_STOCK_DATA_SIGNAL`。
- 股票估值链路：接入东方财富公开行情/估值字段，抓取当前 PE/PB、近三年价格分位、价格回撤和季报披露后涨幅，生成“估值代理分位”；低估值优先排序，但不替代财务尽调。
- 数据纪律：市场热度只作为研究线索，不等同于官方基金经理季报推荐；正式推荐仍需导入官方基金季报和评分数据。
- 基金研究链路：支持从证监会资本市场统一信息披露平台公募基金频道按基金代码抓取季度报告，下载官方 PDF，解析前十大股票持仓，并保留结构化 JSON 导入能力。
- 三重确认：基金经理信号、股票基本面、估值和价格三层同时通过才进入三重确认；缺少基本面或估值证据时只进入观察池，触发利润下滑、应收/存货/商誉异常、治理风险、行业衰退或披露后涨幅超过 30% 时排除。
- 持仓动作提示：持仓表右侧显示“右侧加仓观察 / 止损卖出复核 / 暂停加仓”等动作，并区分“基本盘风险”“价格破位”“震荡洗盘观察”。基本盘风险优先级高于价格波动解释。
- 建仓候选提醒：高分或低估值股票会进入 BUILD_POSITION 提醒；系统只提示观察、试探建仓或等待确认，不自动下单。

后台启动后会按配置周期刷新本地缓存。前端只访问本地 API，避免浏览器直接请求外部源导致 CORS、风控或空白页面。

## 快速启动

Windows 双击 `START_WEB_WINDOWS.cmd`，或执行：

```bash
set PYTHONPATH=src
python -m astock_terminal --no-browser
```

浏览器访问 `http://127.0.0.1:8765`。

首次打开需要创建账户。第一个账户会成为管理员，并接管本机旧版单用户持仓数据；后续账户可以自行注册，持仓、观察池、资金设置和提醒按账户隔离，新账户的持仓页为空。

## 公开仓库与隐私

- 可以将源码发布到公开 GitHub 仓库。
- 不要提交 `.runtime/`、`data/`、`config.json`、`.env`、`*.db`、日志或备份目录。
- 个人持仓、账户、会话和本地配置都保存在运行目录的 SQLite 数据库中，不属于源码。
- 登录密码只保存 PBKDF2 哈希；不要把 GitHub token、网页登录密码或券商截图放进仓库。

## 关键 API

- `POST /api/auth/register`：创建账户；第一个账户会接管本机旧持仓。
- `POST /api/auth/login`：登录并设置 HttpOnly 会话 Cookie。
- `POST /api/auth/logout`：退出登录。
- `POST /api/data/refresh`：刷新行情、a-stock-data 市场信号和新闻源。
- `POST /api/refresh`：仅刷新行情。
- `POST /api/selection/refresh`：刷新东方财富人气榜市场线索，并同步行情。
- `POST /api/stocks/valuation/refresh`：刷新官方基金持仓股、评分股和观察池 A 股的估值代理数据。
- `POST /api/stocks/due-diligence/import`：导入股票基本面/估值尽调数据，用于三重确认评分。
- `POST /api/news/refresh`：刷新东方财富新闻源和用户配置的 RSS/JSON 新闻源。
- `POST /api/positions/recognize`：识别截图或 OCR 文本中的持仓。
- `POST /api/fund/import-official`：导入官方基金季报解析后的结构化数据。
- `POST /api/fund/sync`：按基金代码自动抓取官方季度报告，或按配置的官方结构化 JSON 源同步。
- `GET /api/fund/research`：返回基金经理、产品、季报、持仓、股票评分、买卖计划和产业链分析。
- `GET /api/fund/research-report`：导出 Markdown 专业研究报告。

## 配置

复制 `config.example.json` 到运行目录的 `config.json` 后可调整：

- `refresh_seconds`：行情刷新周期。
- `quote_stale_seconds`：行情有效期。
- `a_stock_data.eastmoney_min_interval`：东方财富请求最小间隔，建议不低于 1.2 秒。
- `a_stock_data.news_refresh_seconds`：新闻后台刷新周期。
- `a_stock_data.signal_refresh_seconds`：市场热度信号后台刷新周期。
- `a_stock_data.valuation_enabled`：是否启用股票估值代理数据刷新。
- `a_stock_data.valuation_lookback_days`：估值代理分位的价格历史回看天数。
- `a_stock_data.undervalued_percentile`：低估值优先阈值，默认 35。
- `fund_report_sync.enabled`：是否启用基金季报后台同步。
- `fund_report_sync.fund_codes`：后台自动抓取的基金代码列表；前端手动同步可直接输入代码，不依赖此项。
- `fund_report_sync.discover_latest`：是否从官方披露列表自动发现最新季度报告基金代码。
- `fund_report_sync.discover_latest_limit`：自动发现基金代码数量上限。
- `fund_report_sync.max_reports_per_fund`：每只基金最多同步的季度报告数。
- `fund_report_sync.report_years`：限制查询年度，留空时由官方接口按基金代码回溯。
- `fund_report_sync.parse_pdf`：是否尝试解析官方 PDF 持仓表。
- `fund_report_sync.structured_json_sources`：官方季报解析后的结构化 JSON 源列表。

结构化导入格式见 `examples/OFFICIAL_DATA_IMPORT_FORMAT.json`。PDF 解析需要可选依赖：

```bash
python -m pip install -r requirements-fund.txt
```

没有 PDF 解析依赖时，系统仍会导入官方报告元数据、PDF URL 和解析状态，但不会生成持仓共识。基金经理姓名和任职日期来自季报 PDF 中“基金经理/投资经理”表，并按报告期归因；离任日期早于报告期末的经理不会归因到该报告期。季报披露存在滞后，前十大持仓外的变化不能由公开季报直接确认；仅因上期前十大未出现而本期出现的股票会标记为“新增可见”，不等同于确认新增。

## 开发与测试

```bash
python -m unittest discover -s tests -v
```

启用 mootdx：

```bash
python -m pip install -e ".[market]"
```

启用截图 OCR：

```bash
python -m pip install -e ".[ocr]"
```

截图识别会依次尝试 `pytesseract`、系统 `tesseract` 命令行和 Windows OCR。若本机没有可用 OCR 引擎，可直接把券商软件识别出的持仓文本粘贴到“持仓截图识别”的 OCR 文本框。

## 数据原则

没有可靠来源时保持空白或显示明确的源异常；双行情源未通过时禁止强买入信号；软件不自动下单。代码采用 MIT 许可证，第三方行情和新闻数据许可不随代码许可证转移。
