# 数据合同

## Quote

必填：`symbol`、`exchange`、`last_price`、`quote_time`、`fetch_time`、`provider`、`status`。

失败状态不得返回伪价格。建议状态：`OK`、`NO_DATA`、`STALE`、`TIMEOUT`、`RATE_LIMITED`、`SCHEMA_CHANGED`、`AUTH_FAILED`、`PROVIDER_UNAVAILABLE`。

## 新闻事件

必填：标题、来源、发布时间、原文链接、来源等级、关联股票、验证状态。转载新闻必须记录 `source_root`，同一原始来源不重复计数。

## 持仓截图识别

识别结果必须包含 `symbol`、`quantity`、`average_cost`。`name`、`theme`、`stop_price`、`take_profit_price` 可为空。截图OCR只能作为录入辅助，导入前必须人工复核数量和成本价。

## 选股证据

基金经理与持仓数据必须包含报告期、原始来源、证据状态和更新时间。季度前十大首次出现只能标记“新增可见”。
