# 官方基金季度报告自动同步（可选组件）

基础网页版不依赖第三方包，可直接运行。当前内置了证监会资本市场统一信息披露平台公募基金频道的低频抓取器，可按基金代码查询季度报告并下载官方 PDF。PDF 表格解析依赖 `pdfplumber`/`pypdf`，未安装时只导入报告元数据并标记解析状态，避免阻塞主程序启动。

## 接入原则

1. 只抓取监管披露平台、基金电子披露网站或基金公司官网。
2. 保存原始 URL、公告日期、报告期、PDF SHA-256、解析页码和解析状态。
3. 季报未进入前十大持仓时只能标记“新增可见”，不能标记“确认新增”。
4. 解析失败、验证码、页面改版或字段缺失时停止入库，不使用推测值补齐。
5. 核验后的结构化结果提交至 `/api/fund/import-official`，或配置到 `fund_report_sync.structured_json_sources` 后由 `/api/fund/sync` 定时拉取。

## 官方平台自动抓取

前端“基金研究”页可输入多个基金代码并点击“同步基金季报”。后台定时抓取可在 `config.json` 中配置：

```json
{
  "fund_report_sync": {
    "enabled": true,
    "fund_codes": ["410007"],
    "max_reports_per_fund": 4,
    "report_years": [],
    "parse_pdf": true,
    "official_min_interval": 0.6
  }
}
```

安装 PDF 解析依赖：

```bash
python -m pip install -r requirements-fund.txt
```

## 输出格式

参考根目录 `examples/OFFICIAL_DATA_IMPORT_FORMAT.json`，核心字段包括：

- `managers`：基金经理、公司、任期、评分、证据状态。
- `products`：基金代码、产品名称、经理、分类、基准、规模、费率。
- `reports`：报告期、公告日、官方 URL、PDF 哈希、解析状态。
- `holdings`：股票代码、持仓排名、市值、占净值比例、份额变化、新增/增持证据。

主程序会基于这些结构化数据自动生成：

- 高分基金经理排名；
- 多基金共同新增/增持股票共识；
- 股票综合评分；
- 规则化买卖点和仓位上限；
- 产业链剖析和 Markdown 专业研究报告。

基金经理姓名和任职日期来自官方季报 PDF 的“基金经理/投资经理”表，并按报告期归因；离任日期早于报告期末的经理不会归因到该报告期。
