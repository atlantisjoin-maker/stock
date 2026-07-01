const $ = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  "\"": "&quot;",
  "'": "&#39;"
}[char]));
const num = value => {
  if (value === null || value === undefined || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
};
const fmt = (value, digits = 2) => {
  const n = num(value);
  return n === null ? "—" : n.toLocaleString("zh-CN", { maximumFractionDigits: digits });
};
const pct = value => {
  const n = num(value);
  return n === null ? "—" : `${(n * 100).toFixed(1)}%`;
};
const pctPoint = value => {
  const n = num(value);
  return n === null ? "—" : `${fmt(n)}%`;
};
const money = value => {
  const n = num(value);
  return n === null ? "—" : `¥${fmt(n)}`;
};
const badge = value => {
  const raw = String(value || "—");
  const cls = raw.replace(/[^a-zA-Z0-9_-]/g, "_");
  return `<span class="badge ${cls}">${esc(raw)}</span>`;
};
const flagsText = value => {
  if (!value) return "—";
  if (Array.isArray(value)) return value.length ? value.join("；") : "—";
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) && parsed.length ? parsed.join("；") : "—";
  } catch {
    return String(value);
  }
};
const valuationOf = x => x.valuation_detail || {};
const tradePlanOf = x => x.trade_plan || {};
const buyZoneText = p => {
  if (!p || (p.buy_zone_low == null && p.buy_zone_high == null)) return "—";
  return `${fmt(p.buy_zone_low, 4)}-${fmt(p.buy_zone_high, 4)}`;
};
const tradePointStatus = p => {
  const items = p?.proximity || [];
  return items.length ? items.map(x => x.label || x.status).join("；") : "—";
};

async function api(path, opt = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opt });
  const text = await response.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    data = text;
  }
  if (response.status === 401) {
    showAuth(data.auth || null, data.error || "请先登录");
    throw new Error(data.error || "请先登录");
  }
  if (!response.ok) {
    throw new Error(typeof data === "string" ? data : (data.message || data.error || response.statusText));
  }
  return data;
}

let STATE = null;
let PENDING_POSITIONS = [];
let PENDING_ACCOUNT_SUMMARY = {};
let AUTH = null;

function showAuth(status = null, message = "") {
  AUTH = status;
  $("appShell").hidden = true;
  $("authGate").hidden = false;
  $("authMessage").textContent = message || (status && status.user_count === 0 ? "首次使用请创建管理员账户。" : "");
}

function showApp(auth) {
  AUTH = auth;
  $("authGate").hidden = true;
  $("appShell").hidden = false;
  $("currentUser").textContent = auth?.user?.username ? `账户 ${auth.user.username}` : "";
}

async function authStatus() {
  const response = await fetch("/api/auth/status", { headers: { "Content-Type": "application/json" } });
  return response.json();
}

async function startAuthenticated(auth) {
  showApp(auth);
  await loadWithBootstrap();
}

function table(headers, rows) {
  if (!rows.length) return '<div class="empty">暂无可靠数据</div>';
  return `<div class="table-wrap"><table><thead><tr>${headers.map(h => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
}

function guideHtml(d) {
  const quoteCount = d.quotes.length;
  const watchCount = d.watchlist.length;
  const status = d.selection.status;
  const tencent = (d.source_health || []).find(x => x.name === "tencent");
  const hot = (d.source_health || []).find(x => x.name === "a-stock-data:hot_rank");
  if (!watchCount) return "已自动准备基础观察池。点击“刷新全部数据源”后显示实时行情、新闻和市场信号。";
  if (!quoteCount) return `观察池 ${watchCount} 个代码等待刷新。点击“刷新全部数据源”获取最新行情。`;
  if (status === "MARKET_SIGNAL_ONLY") return `已加载 ${quoteCount} 条行情，并接入 a-stock-data 市场热度线索；这不是官方基金经理持仓推荐。热榜源状态：${hot ? hot.status : "未请求"}。`;
  if (status === "WATCHLIST_ONLY") return `行情已加载 ${quoteCount} 条，当前仅为观察池待研究列表。腾讯源状态：${tencent ? tencent.status : "未请求"}。`;
  return `行情已加载 ${quoteCount} 条，选股系统状态：${status}。`;
}

function render() {
  const d = STATE;
  const p = d.portfolio;
  const s = d.selection;
  $("mWatch").textContent = d.watchlist.length;
  $("mPositions").textContent = p.position_count;
  $("mWeight").textContent = pct(p.invested_weight);
  $("mPnl").textContent = money(p.unrealized_pnl);
  $("mPnl").className = (p.unrealized_pnl || 0) >= 0 ? "positive" : "negative";
  $("mCandidates").textContent = s.status === "WATCHLIST_ONLY" ? s.stocks.length : s.stocks.filter(x => num(x.total_score) !== null && Number(x.total_score) >= 70).length;
  $("mAlerts").textContent = d.alerts.length;
  $("watchInput").value = d.watchlist.join(",");
  $("dataGuide").textContent = guideHtml(d);

  $("dashboardCandidates").innerHTML = buildCandidatesHtml(d.build_candidates || [], s.stocks);
  $("dashboardAlerts").innerHTML = alertsHtml(d.alerts.slice(0, 6));
  $("dashboardPortfolio").innerHTML = `${portfolioSummaryHtml(p, true)}${positionsHtml(p.positions.slice(0, 6))}`;
  $("dashboardSources").innerHTML = sourcesHtml(d.source_health);
  $("quoteTable").innerHTML = quotesHtml(d.quotes);
  $("selectionStatus").textContent = s.message;
  $("managerTable").innerHTML = managersHtml(s.managers);
  $("consensusTable").innerHTML = consensusHtml(s.consensus);
  $("stockScoreTable").innerHTML = scoresHtml(s.stocks);
  renderFundResearch(d.fund || {});
  $("portfolioSummary").innerHTML = portfolioSummaryHtml(p);
  $("positionTable").innerHTML = positionsHtml(p.positions);
  $("newsList").innerHTML = newsHtml(d.events);
  $("alertList").innerHTML = alertsHtml(d.alerts);
  fillSettings(d.settings);
}

function summaryItem(label, value, formatter = money, className = "") {
  return `<div class="summary-item ${esc(className)}"><span>${esc(label)}</span><b>${formatter(value)}</b></div>`;
}

function portfolioSummaryHtml(p, compact = false) {
  const a = p.account_summary || {};
  const dailyClass = (a.daily_pnl || 0) >= 0 ? "positive" : "negative";
  const pnlClass = (a.holding_pnl || 0) >= 0 ? "positive" : "negative";
  const cells = [
    summaryItem("资金余额", a.cash_balance),
    summaryItem("可取金额", a.withdrawable_cash),
    summaryItem("持仓盈亏", a.holding_pnl, money, pnlClass),
    summaryItem("冻结金额", a.frozen_cash),
    summaryItem("股票市值", a.stock_market_value),
    summaryItem("当日盈亏", a.daily_pnl, money, dailyClass),
    summaryItem("可用金额", a.available_cash),
    summaryItem("总资产", a.total_asset),
    summaryItem("当日盈亏比", a.daily_pnl_pct, pct, dailyClass),
  ].join("");
  const footer = `<p class="summary-foot">组合仓位 <b>${pct(p.invested_weight)}</b> · 股票仓位 <b>${pct(p.stock_invested_weight)}</b> · 股票基准 ${money(p.stock_capital_base)} · 整体基准 ${money(p.capital_base)}</p>`;
  return `<div class="summary-grid ${compact ? "compact" : ""}">${cells}</div>${footer}`;
}

function quotesHtml(rows) {
  if (!rows.length) return '<div class="empty">暂无行情。请先输入观察池代码或导入持仓，再点击“立即刷新行情”。</div>';
  return table(["代码", "名称", "最新价", "验证", "主源", "校验源", "偏差", "原因"], rows.map(r => `<tr><td>${esc(r.symbol)}</td><td>${esc(r.name || "—")}</td><td>${fmt(r.last_price)}</td><td>${badge(r.level)}</td><td>${esc(r.primary_provider || "—")}</td><td>${esc(r.secondary_provider || "—")}</td><td>${r.deviation == null ? "—" : `${(r.deviation * 100).toFixed(3)}%`}</td><td>${esc((r.reasons || []).join("；"))}</td></tr>`));
}

function managersHtml(rows) {
  return table(["经理", "公司", "得分", "任期", "报告期", "证据"], rows.map(x => `<tr><td>${esc(x.name)}</td><td>${esc(x.company)}</td><td>${fmt(x.score)}</td><td>${fmt(x.tenure_years)}年</td><td>${esc(x.report_period || "—")}</td><td>${badge(x.evidence_status)}</td></tr>`));
}

function consensusHtml(rows) {
  return table(["代码", "名称", "经理数", "公司数", "确认增持", "连续增持", "优秀经理", "三重确认", "共识分", "证据"], rows.map(x => `<tr><td>${esc(x.symbol)}</td><td>${esc(x.name || "—")}</td><td>${fmt(x.manager_count, 0)}</td><td>${fmt(x.company_count, 0)}</td><td>${fmt(x.confirmed_increase, 0)}</td><td>${fmt(x.consecutive_increase, 0)}</td><td>${fmt(x.excellent_manager_count, 0)}</td><td>${badge(x.triple_confirm_status)}</td><td>${fmt(x.consensus_score)}</td><td>${badge(x.evidence_status)}</td></tr>`));
}

function scoresHtml(rows) {
  if (!rows.length) return '<div class="empty">暂无正式股票评分。刷新市场信号后可先查看 a-stock-data 热度线索；正式推荐仍需导入官方基金季报和评分数据。</div>';
  return table(["代码", "名称", "行业", "估值分位", "PE", "PB", "回撤", "经理层", "基本面", "估值层", "总分", "等级", "买点", "卖点", "止损", "状态", "来源"], rows.map(x => {
    const v = valuationOf(x);
    const p = tradePlanOf(x);
    return `<tr><td>${esc(x.symbol)}</td><td>${esc(x.name || "—")}</td><td>${esc(x.industry || "—")}</td><td>${fmt(v.valuation_percentile ?? x.valuation)}</td><td>${fmt(v.pe_ttm)}</td><td>${fmt(v.pb)}</td><td>${pctPoint(v.price_drawdown_pct)}</td><td>${fmt(x.manager_signal)}</td><td>${fmt(x.fundamental_signal)}</td><td>${fmt(x.valuation_signal)}</td><td><b>${fmt(x.total_score)}</b></td><td>${esc(x.grade || "—")}</td><td>${buyZoneText(p)}</td><td>${fmt(p.sell_point, 4)}</td><td>${fmt(p.stop_loss ?? p.stop, 4)}</td><td>${esc(tradePointStatus(p))}</td><td>${badge(x.source_status)}</td></tr>`;
  }));
}

function buildCandidatesHtml(candidates, fallbackRows) {
  const rows = candidates.length ? candidates.map(x => {
    const p = tradePlanOf(x);
    return `<tr><td>${esc(x.symbol)}</td><td>${esc(x.name || "—")}</td><td>${fmt(x.total_score)}</td><td>${fmt(x.valuation_percentile)}</td><td>${fmt(x.pe_ttm)}</td><td>${fmt(x.pb)}</td><td>${pctPoint(x.price_drawdown_pct)}</td><td>${badge(x.triple_confirm_status)}</td><td>${esc(x.action || "—")}</td><td>${buyZoneText(p)}</td><td>${fmt(p.sell_point, 4)}</td><td>${fmt(p.stop_loss ?? p.stop, 4)}</td><td>${esc(tradePointStatus(p))}</td></tr>`;
  }) : fallbackRows.slice(0, 8).map(x => {
    const p = tradePlanOf(x);
    return `<tr><td>${esc(x.symbol)}</td><td>${esc(x.name || "—")}</td><td>${fmt(x.total_score)}</td><td>${fmt(x.consensus_score)}</td><td>—</td><td>—</td><td>—</td><td>${badge(x.source_status)}</td><td>${esc(x.grade || "—")}</td><td>${buyZoneText(p)}</td><td>${fmt(p.sell_point, 4)}</td><td>${fmt(p.stop_loss ?? p.stop, 4)}</td><td>${esc(tradePointStatus(p))}</td></tr>`;
  });
  return table(["代码", "名称", "总分", "估值分位", "PE", "PB", "回撤", "确认", "动作", "买点", "卖点", "止损", "状态"], rows);
}

function renderFundResearch(fund) {
  if (!$("fundResearchStatus")) return;
  $("fundResearchStatus").textContent = fund.message || "尚未导入官方基金季报结构化数据。";
  $("fundManagerRank").innerHTML = table(["经理", "公司", "得分", "任期", "报告期", "证据"], (fund.managers || []).slice(0, 20).map(x => `<tr><td>${esc(x.name)}</td><td>${esc(x.company)}</td><td>${fmt(x.score)}</td><td>${fmt(x.tenure_years)}年</td><td>${esc(x.report_period || "—")}</td><td>${badge(x.evidence_status)}</td></tr>`));
  $("fundReportTable").innerHTML = table(["报告期", "基金", "经理", "公告日", "覆盖率", "解析", "证据"], (fund.reports || []).slice(0, 30).map(x => `<tr><td>${esc(x.report_period)}</td><td>${esc(x.fund_code)} ${esc(x.fund_name || "")}</td><td>${esc(x.manager_name || "—")}</td><td>${esc(x.announcement_date || "—")}</td><td>${fmt(x.coverage)}</td><td>${badge(x.parser_status)}</td><td>${badge(x.evidence_status)}</td></tr>`));
  $("fundTradePlans").innerHTML = table(["代码", "名称", "估值分位", "PE", "PB", "回撤", "三重确认", "排除项", "操作", "买点", "卖点", "止损", "仓位上限"], (fund.stocks || []).slice(0, 30).map(x => {
    const p = x.trade_plan || {};
    const v = valuationOf(x);
    return `<tr><td>${esc(x.symbol)}</td><td>${esc(x.name || "—")}</td><td>${fmt(v.valuation_percentile ?? x.valuation)}</td><td>${fmt(v.pe_ttm)}</td><td>${fmt(v.pb)}</td><td>${pctPoint(v.price_drawdown_pct)}</td><td>${badge(x.triple_confirm_status)}</td><td>${esc(flagsText(x.exclusion_flags))}</td><td>${esc(p.action || "—")}</td><td title="${esc(p.entry || "")}">${buyZoneText(p)}</td><td>${fmt(p.sell_point, 4)}</td><td>${fmt(p.stop_loss ?? p.stop, 4)}</td><td>${pct(p.max_weight)}</td></tr>`;
  }));
  const chainCards = (fund.stocks || []).slice(0, 8).map(x => {
    const c = x.industry_chain || {};
    return `<article class="chain-card"><h3>${esc(x.symbol)} ${esc(x.name || "")} · ${esc(c.chain || "产业链")}</h3><p><b>上游：</b>${esc(c.upstream || "—")}</p><p><b>中游：</b>${esc(c.midstream || "—")}</p><p><b>下游：</b>${esc(c.downstream || "—")}</p><p><b>催化：</b>${esc((c.catalysts || []).join("；"))}</p><p><b>风险：</b>${esc((c.risks || []).join("；"))}</p><p><b>验证：</b>${esc((c.checks || []).join("；"))}</p></article>`;
  }).join("");
  $("industryChainView").innerHTML = chainCards || '<div class="empty">暂无可剖析股票。导入官方季报后显示产业链框架。</div>';
}

function stockConsultationHtml(data) {
  if (!data || !data.ok) return '<div class="empty">请输入股票代码后开始分析。</div>';
  const q = data.quote || {};
  const s = data.score || {};
  const v = data.valuation || {};
  const c = data.consensus || {};
  const p = data.trade_plan || {};
  const chain = data.industry_chain || {};
  const warnings = (data.warnings || []).map(x => `<div class="notice">${esc(x)}</div>`).join("");
  const quoteRows = [
    `<tr><td>代码</td><td>${esc(data.symbol)}</td><td>名称</td><td>${esc(data.name || q.name || "—")}</td></tr>`,
    `<tr><td>最新价</td><td>${fmt(q.last_price, 4)}</td><td>行情状态</td><td>${badge(q.level || "—")}</td></tr>`,
    `<tr><td>更新时间</td><td>${esc(q.updated_at || "—")}</td><td>来源</td><td>${esc(q.primary_provider || "—")} / ${esc(q.secondary_provider || "—")}</td></tr>`,
  ];
  const scoreRows = [
    `<tr><td>总分</td><td><b>${fmt(s.total_score)}</b></td><td>等级</td><td>${esc(s.grade || "—")}</td></tr>`,
    `<tr><td>来源</td><td>${badge(s.source_status || "—")}</td><td>三重确认</td><td>${badge(s.triple_confirm_status || "—")}</td></tr>`,
    `<tr><td>经理层</td><td>${fmt(s.manager_signal)}</td><td>基本面</td><td>${fmt(s.fundamental_signal)}</td></tr>`,
    `<tr><td>估值层</td><td>${fmt(s.valuation_signal ?? s.valuation)}</td><td>风险</td><td>${fmt(s.risk)}</td></tr>`,
    `<tr><td>估值分位</td><td>${fmt(v.valuation_percentile)}</td><td>PE/PB</td><td>${fmt(v.pe_ttm)} / ${fmt(v.pb)}</td></tr>`,
    `<tr><td>回撤</td><td>${pctPoint(v.price_drawdown_pct)}</td><td>基金共识</td><td>${fmt(c.consensus_score)}</td></tr>`,
  ];
  const planRows = [
    `<tr><td>操作</td><td>${esc(p.action || "—")}</td><td>仓位上限</td><td>${pct(p.max_weight)}</td></tr>`,
    `<tr><td>买点区间</td><td>${buyZoneText(p)}</td><td>突破观察</td><td>${fmt(p.breakout_point, 4)}</td></tr>`,
    `<tr><td>卖点</td><td>${fmt(p.sell_point ?? p.take_profit_primary, 4)}</td><td>止损</td><td>${fmt(p.stop_loss ?? p.stop, 4)}</td></tr>`,
    `<tr><td>止盈分批</td><td>${esc(p.take_profit || "—")}</td><td>接近状态</td><td>${esc(tradePointStatus(p))}</td></tr>`,
    `<tr><td>买入观察</td><td colspan="3">${esc(p.entry || "—")}</td></tr>`,
    `<tr><td>分批</td><td colspan="3">${esc(p.tranche || "—")}</td></tr>`,
    `<tr><td>原因</td><td colspan="3">${esc(p.reason || s.scoring_notes || "—")}</td></tr>`,
  ];
  const holdings = table(["报告期", "基金", "经理", "持仓变化", "证据"], (data.fund_holdings || []).slice(0, 8).map(x => `<tr><td>${esc(x.report_period || "—")}</td><td>${esc(x.fund_code || "")} ${esc(x.fund_name || "")}</td><td>${esc(x.manager_name || "—")}</td><td>${fmt(x.change_shares, 0)}</td><td>${badge(x.evidence_status || "—")}</td></tr>`));
  const news = newsHtml(data.events || []);
  const chainHtml = `<article class="chain-card"><h3>${esc(data.symbol)} ${esc(data.name || "")} · ${esc(chain.chain || "产业链")}</h3><p><b>上游：</b>${esc(chain.upstream || "—")}</p><p><b>中游：</b>${esc(chain.midstream || "—")}</p><p><b>下游：</b>${esc(chain.downstream || "—")}</p><p><b>催化：</b>${esc((chain.catalysts || []).join("；"))}</p><p><b>风险：</b>${esc((chain.risks || []).join("；"))}</p><p><b>验证：</b>${esc((chain.checks || []).join("；"))}</p></article>`;
  return `${warnings}<div class="grid two"><article class="panel"><header><h2>行情与评分</h2><span>与行情/选股系统同源</span></header>${table(["项目", "值", "项目", "值"], quoteRows)}${table(["项目", "值", "项目", "值"], scoreRows)}</article><article class="panel"><header><h2>交易计划</h2><span>规则化复核，不自动下单</span></header>${table(["项目", "值", "项目", "值"], planRows)}</article></div><div class="grid two"><article class="panel"><header><h2>产业链剖析</h2><span>按行业关键词生成验证框架</span></header>${chainHtml}</article><article class="panel"><header><h2>基金持仓证据</h2><span>官方季报入库后显示</span></header>${holdings}</article></div><article class="panel"><header><h2>相关新闻</h2><span>用于风险和催化复核</span></header>${news}</article>`;
}

function positionsHtml(rows) {
  return table(["类型", "代码", "名称", "题材", "数量", "成本", "现价", "市值", "盈亏", "仓位", "买点", "卖点", "止损", "接近状态", "行情", "动作", "诊断", "操作"], rows.map(x => {
    const a = x.position_action || {};
    const d = x.daily_trade_plan || {};
    const encoded = encodeURIComponent(String(x.symbol || ""));
    const buyPoint = d.buy_zone_low == null && d.buy_zone_high == null ? "—" : `${fmt(d.buy_zone_low, 4)}-${fmt(d.buy_zone_high, 4)}`;
    return `<tr><td>${esc(x.asset_type_label || x.asset_type || "股票")}</td><td>${esc(x.symbol)}</td><td>${esc(x.name || "—")}</td><td>${esc(x.theme)}</td><td>${fmt(x.quantity, 4)}</td><td>${fmt(x.average_cost, 4)}</td><td>${fmt(x.current_price, 4)}</td><td>${money(x.market_value)}</td><td class="${(x.unrealized_pnl || 0) >= 0 ? "positive" : "negative"}">${money(x.unrealized_pnl)} / ${pct(x.pnl_pct)}</td><td>${pct(x.portfolio_weight)}</td><td title="${esc(d.summary || "")}">${buyPoint}</td><td>${fmt(d.sell_point, 4)}</td><td>${fmt(d.stop_loss ?? x.effective_stop_loss ?? x.stop_price, 4)}</td><td>${esc(tradePointStatus(d))}</td><td>${badge(x.quote_level)}</td><td>${badge(a.action || "—")}</td><td>${esc(a.diagnosis || "—")}</td><td><button class="mini" onclick="deletePosition('${esc(encoded)}')">删除</button></td></tr>`;
  }));
}

function accountSummaryHtml(summary) {
  if (!summary || !Object.keys(summary).length) return "";
  const map = {
    account_cash_balance: "资金余额",
    account_withdrawable_cash: "可取金额",
    account_frozen_cash: "冻结金额",
    account_available_cash: "可用金额",
    account_stock_market_value: "股票市值",
    account_total_asset: "总资产",
    account_holding_pnl: "持仓盈亏",
    account_daily_pnl: "当日盈亏",
    account_daily_pnl_pct: "当日盈亏比",
  };
  const rows = Object.entries(map)
    .filter(([key]) => summary[key] !== undefined && summary[key] !== null)
    .map(([key, label]) => `<tr><td>${esc(label)}</td><td>${key === "account_daily_pnl_pct" ? pct(summary[key]) : money(summary[key])}</td></tr>`);
  return `<div class="result"><h3>识别到账户资金汇总</h3>${table(["项目", "数值"], rows)}</div>`;
}

function recognizedHtml(result) {
  const rows = (result.positions || []).map(x => `<tr><td>${esc(x.asset_type_label || x.asset_type || "股票")}</td><td>${esc(x.symbol)}</td><td>${esc(x.name || "—")}</td><td>${fmt(x.quantity, 4)}</td><td>${fmt(x.average_cost, 4)}</td><td>${fmt(x.current_price, 4)}</td><td>${esc(x.theme || "未分类")}</td></tr>`);
  const warnings = (result.warnings || []).map(x => `<div class="notice">${esc(x)}</div>`).join("");
  const text = result.text ? `<details class="ocr-text"><summary>查看 OCR 原文</summary><pre>${esc(result.text)}</pre></details>` : "";
  return `${warnings}${result.engine ? `<p>OCR 引擎：${esc(result.engine)}</p>` : ""}${text}${accountSummaryHtml(result.account_summary)}${table(["类型", "代码", "名称", "数量", "成本", "现价", "题材"], rows)}`;
}

function fileDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

function newsHtml(rows) {
  if (!rows.length) return '<div class="empty">尚未导入可靠新闻。点击“刷新新闻源”拉取东财全球资讯与个股新闻。</div>';
  return rows.map(x => `<article class="event"><div>${badge(x.verification)} ${badge(x.source_level)} <small>${esc(x.source)} · ${esc(x.published_at)}</small></div><h3>${esc(x.title)}</h3><p>股票 ${esc((x.symbols || []).join(",") || "未映射")} · 题材 ${esc((x.themes || []).join(",") || "未分类")} · 机会 ${fmt(x.opportunity_score)} / 风险 ${fmt(x.risk_score)}</p>${x.original_url ? `<a href="${esc(x.original_url)}" target="_blank" rel="noreferrer">查看原文</a>` : ""}</article>`).join("");
}

function alertsHtml(rows) {
  if (!rows.length) return '<div class="empty">当前没有活动提醒</div>';
  return rows.map(x => `<article class="alert ${esc(x.severity)}"><div>${badge(x.severity)} ${badge(x.category)} <small>${esc(x.created_at)}</small></div><h3>${x.symbol ? `${esc(x.symbol)} · ` : ""}${esc(x.title)}</h3><p>${esc(x.message)}</p><p><b>建议：</b>${esc(x.action)}</p><button class="mini" onclick="ackAlert('${esc(x.alert_id)}')">标记已读</button></article>`).join("");
}

function sourcesHtml(rows) {
  if (!rows.length) return '<div class="empty">尚未请求数据源</div>';
  return rows.map(x => `<div class="source"><b>${esc(x.name)}</b> ${badge(x.status)}<br><small>延迟 ${fmt(x.latency_ms, 0)}ms · 最近成功 ${esc(x.last_success || "无")}${x.error ? `<br>${esc(x.error)}` : ""}</small></div>`).join("");
}

function fillSettings(settings) {
  const form = $("settingsForm");
  for (const [key, value] of Object.entries(settings || {})) {
    const field = form.elements[key];
    if (!field) continue;
    if (field.type === "checkbox") field.checked = Boolean(value);
    else field.value = key === "account_daily_pnl_pct" && value !== null && value !== undefined ? Number(value) * 100 : value;
  }
}

async function load() {
  STATE = await api("/api/dashboard");
  if (STATE.auth) showApp(STATE.auth);
  render();
  $("serverDot").classList.add("ok");
  $("serverText").textContent = "本地服务正常";
  $("clock").textContent = STATE.server_time.replace("T", " ");
}

async function loadWithBootstrap() {
  await load();
  if (STATE.watchlist.length && STATE.quotes.length === 0) {
    $("serverText").textContent = "正在刷新行情";
    try {
      await api("/api/refresh", { method: "POST", body: "{}" });
      await load();
    } catch (error) {
      $("dataGuide").textContent = `行情刷新失败：${error.message}`;
    }
  }
}

async function withBusy(button, label, fn) {
  const oldText = button.textContent;
  button.disabled = true;
  button.textContent = label;
  try {
    return await fn();
  } finally {
    button.disabled = false;
    button.textContent = oldText;
  }
}

window.deletePosition = async encodedSymbol => {
  const symbol = decodeURIComponent(encodedSymbol);
  if (confirm(`删除持仓 ${symbol}？`)) {
    await api(`/api/positions/${encodeURIComponent(symbol)}`, { method: "DELETE" });
    await load();
  }
};
window.ackAlert = async id => {
  await api("/api/alerts/ack", { method: "POST", body: JSON.stringify({ alert_id: id }) });
  await load();
};

$("authForm").onsubmit = async event => {
  event.preventDefault();
  const submitter = event.submitter;
  const mode = submitter?.value || "login";
  const form = new FormData(event.currentTarget);
  const payload = Object.fromEntries(form.entries());
  $("authMessage").textContent = "";
  try {
    const result = await api(`/api/auth/${mode}`, { method: "POST", body: JSON.stringify(payload) });
    await startAuthenticated(result.auth);
  } catch (error) {
    $("authMessage").textContent = error.message;
  }
};

$("logoutBtn").onclick = async () => {
  await api("/api/auth/logout", { method: "POST", body: "{}" }).catch(() => null);
  STATE = null;
  PENDING_POSITIONS = [];
  const status = await authStatus().catch(() => null);
  showAuth(status, "已退出登录。");
};

document.querySelectorAll(".nav").forEach(button => {
  button.onclick = () => {
    document.querySelectorAll(".nav").forEach(x => x.classList.remove("active"));
    document.querySelectorAll(".page").forEach(x => x.classList.remove("active"));
    button.classList.add("active");
    $(`page-${button.dataset.page}`).classList.add("active");
  };
});

$("refreshBtn").onclick = async event => withBusy(event.currentTarget, "刷新中", async () => {
  const result = await api("/api/data/refresh", { method: "POST", body: "{}" });
  await load();
  const signals = result.selection?.imported || 0;
  const news = result.news?.imported || 0;
  const valuation = result.valuation?.imported || 0;
  alert(`刷新完成：市场信号 ${signals} 条，估值 ${valuation} 条，新闻 ${news} 条。`);
});

$("marketRefresh").onclick = async event => withBusy(event.currentTarget, "刷新中", async () => {
  await api("/api/refresh", { method: "POST", body: "{}" });
  await load();
});

if ($("consultBtn")) {
  $("consultBtn").onclick = async event => withBusy(event.currentTarget, "分析中", async () => {
    const symbol = $("consultSymbol").value.trim();
    if (!symbol) {
      alert("请输入 6 位股票代码");
      return;
    }
    const result = await api("/api/stocks/consult", {
      method: "POST",
      body: JSON.stringify({ symbol, refresh: $("consultRefresh").checked }),
    });
    $("consultResult").innerHTML = stockConsultationHtml(result);
    await load();
  });
}

$("saveWatch").onclick = async () => {
  const symbols = $("watchInput").value.split(/[,，\s]+/).filter(Boolean);
  await api("/api/watchlist", { method: "POST", body: JSON.stringify({ symbols }) });
  await load();
};

$("starterWatch").onclick = async () => {
  const symbols = ["510300", "510500", "159915", "588000", "512100"];
  $("watchInput").value = symbols.join(",");
  await api("/api/watchlist", { method: "POST", body: JSON.stringify({ symbols }) });
  try {
    await api("/api/refresh", { method: "POST", body: "{}" });
  } catch (error) {
    alert(error.message);
  }
  await load();
};

$("fundSyncBtn").onclick = async event => withBusy(event.currentTarget, "刷新中", async () => {
  const result = await api("/api/selection/refresh", { method: "POST", body: "{}" });
  await load();
  alert(`已导入市场信号 ${result.selection?.imported || 0} 条。`);
});

if ($("fundReportSyncBtn")) {
  $("fundReportSyncBtn").onclick = async event => withBusy(event.currentTarget, "同步中", async () => {
    const fundCodes = ($("fundCodeInput")?.value || "").split(/[,，\s]+/).filter(Boolean);
    const discoverLatest = Boolean($("fundDiscoverLatest")?.checked);
    if (!fundCodes.length && !discoverLatest) {
      alert("请输入至少一个 6 位基金代码，或勾选自动发现官方最新季报");
      return;
    }
    const years = ($("fundReportYears")?.value || "").split(/[,，\s]+/).filter(Boolean);
    const maxReports = Number($("fundMaxReports")?.value || 4);
    const discoverLimit = Number($("fundDiscoverLimit")?.value || 10);
    const result = await api("/api/fund/sync", {
      method: "POST",
      body: JSON.stringify({ fund_codes: fundCodes, report_years: years, max_reports_per_fund: maxReports, discover_latest: discoverLatest, discover_latest_limit: discoverLimit })
    });
    await load();
    if (!result.ok) {
      alert(result.message || `基金季报同步状态：${result.status}`);
      return;
    }
    alert(`基金季报同步完成：报告 ${result.imported?.reports || 0} 份，持仓 ${result.imported?.holdings || 0} 条，估值 ${result.valuation?.imported || 0} 条。`);
  });
}

if ($("valuationRefreshBtn")) {
  $("valuationRefreshBtn").onclick = async event => withBusy(event.currentTarget, "刷新中", async () => {
    const result = await api("/api/stocks/valuation/refresh", { method: "POST", body: "{}" });
    await load();
    if (!result.ok) {
      alert(result.error || `估值刷新状态：${result.status}`);
      return;
    }
    alert(`估值刷新完成：${result.imported || 0} 只股票。`);
  });
}

$("newsRefresh").onclick = async event => withBusy(event.currentTarget, "刷新中", async () => {
  const result = await api("/api/news/refresh", { method: "POST", body: "{}" });
  await load();
  alert(`导入新闻 ${result.imported || 0} 条。`);
});

$("positionForm").onsubmit = async event => {
  event.preventDefault();
  const form = new FormData(event.target);
  const payload = Object.fromEntries(form.entries());
  ["quantity", "average_cost", "score"].forEach(key => payload[key] = Number(payload[key]));
  payload.current_price = payload.current_price ? Number(payload.current_price) : null;
  ["stop_price", "take_profit_price"].forEach(key => payload[key] = payload[key] ? Number(payload[key]) : null);
  await api("/api/positions", { method: "POST", body: JSON.stringify(payload) });
  event.target.reset();
  event.target.elements.theme.value = "未分类";
  event.target.elements.score.value = 50;
  await load();
};

$("recognizePositions").onclick = async () => {
  try {
    const file = $("positionScreenshot").files[0];
    const text = $("positionOcrText").value.trim();
    const body = {};
    if (file) body.image_data = await fileDataUrl(file);
    if (text) body.text = text;
    if (!body.image_data && !body.text) {
      alert("请选择截图或粘贴 OCR 文本");
      return;
    }
    const result = await api("/api/positions/recognize", { method: "POST", body: JSON.stringify(body) });
    PENDING_POSITIONS = result.positions || [];
    PENDING_ACCOUNT_SUMMARY = result.account_summary || {};
    $("ocrResult").innerHTML = recognizedHtml(result);
    $("importRecognized").disabled = !PENDING_POSITIONS.length && !Object.keys(PENDING_ACCOUNT_SUMMARY).length;
  } catch (error) {
    alert(error.message);
  }
};

$("importRecognized").onclick = async () => {
  if (!PENDING_POSITIONS.length && !Object.keys(PENDING_ACCOUNT_SUMMARY).length) return;
  const summaryCount = Object.keys(PENDING_ACCOUNT_SUMMARY).length;
  if (!confirm(`导入 ${PENDING_POSITIONS.length} 条识别持仓和 ${summaryCount} 项账户汇总？`)) return;
  const result = await api("/api/positions/recognize", { method: "POST", body: JSON.stringify({ positions: PENDING_POSITIONS, account_summary: PENDING_ACCOUNT_SUMMARY, apply: true }) });
  PENDING_POSITIONS = [];
  PENDING_ACCOUNT_SUMMARY = {};
  $("importRecognized").disabled = true;
  $("ocrResult").innerHTML = `<div class="result">已导入 ${result.imported} 条持仓，更新 ${result.imported_settings || 0} 项账户汇总</div>`;
  await load();
};

$("sizingForm").onsubmit = async event => {
  event.preventDefault();
  const form = new FormData(event.target);
  const payload = Object.fromEntries(form.entries());
  ["total_capital", "current_invested", "current_theme_exposure", "current_stock_exposure", "entry_price", "stop_price", "score"].forEach(key => payload[key] = Number(payload[key]));
  payload.is_new_theme = event.target.elements.is_new_theme.checked;
  payload.lot_size = 100;
  const result = await api("/api/position-size", { method: "POST", body: JSON.stringify(payload) });
  $("sizingResult").innerHTML = `<div class="result"><h3>${result.allowed ? "允许分批执行" : "禁止新增仓位"}</h3><p>${esc(result.reason)}</p><p>可执行金额 ${money(result.executable_amount)} · 股数 ${fmt(result.executable_shares, 0)} · 风险预算 ${money(result.risk_budget)} · 目标仓位 ${pct(result.target_weight)}</p><p>分批 ${result.tranche_amounts.map(money).join(" / ")}</p></div>`;
};

$("settingsForm").onsubmit = async event => {
  event.preventDefault();
  const form = new FormData(event.target);
  const payload = Object.fromEntries(form.entries());
  ["total_capital", "default_stop_loss_pct", "opportunity_min_score", "opportunity_max_risk"].forEach(key => payload[key] = Number(payload[key]));
  [
    "account_cash_balance",
    "account_withdrawable_cash",
    "account_frozen_cash",
    "account_available_cash",
    "account_stock_market_value",
    "account_total_asset",
    "account_holding_pnl",
    "account_daily_pnl",
    "account_daily_pnl_pct",
  ].forEach(key => {
    const field = event.target.elements[key];
    if (field && field.value.trim() !== "") payload[key] = Number(field.value);
  });
  payload.notifications_enabled = event.target.elements.notifications_enabled.checked;
  await api("/api/settings", { method: "POST", body: JSON.stringify(payload) });
  await load();
};

$("notifyTest").onclick = async () => {
  try {
    const result = await api("/api/notifications/test", {
      method: "POST",
      body: JSON.stringify({ channel: $("notifyChannel").value || null, message: $("notifyMessage").value })
    });
    alert(JSON.stringify(result));
  } catch (error) {
    alert(error.message);
  }
};

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/service-worker.js").catch(() => {});
}

async function initApp() {
  const status = await authStatus();
  if (!status.authenticated) {
    showAuth(status);
    return;
  }
  await startAuthenticated(status);
}

initApp().catch(error => {
  showAuth(null, "连接失败：" + error.message);
  console.error(error);
});
setInterval(() => {
  if (!$("appShell").hidden) load().catch(() => {});
}, 15000);
