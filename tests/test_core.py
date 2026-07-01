import json, tempfile, unittest
from pathlib import Path
import sys, os, tempfile
os.environ.setdefault("ASTOCK_HOME", tempfile.mkdtemp(prefix="astock-tests-"))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import web_app
from astock_terminal.providers.fund_eid import parse_fund_detail_html, parse_fund_manager_rows, parse_stock_holdings_from_text, report_period_from_row

class CoreTests(unittest.TestCase):
    def test_exchange_920(self): self.assertEqual(web_app.exchange_of('920001'),'BSE')
    def test_exchange_sh(self): self.assertEqual(web_app.exchange_of('600000'),'SSE')
    def test_normalize(self): self.assertEqual(web_app.normalize_symbol('SH.600000'),'600000')
    def test_event_official(self): self.assertEqual(web_app.verification_for_event('A',1),'CONFIRMED')
    def test_event_two_media(self): self.assertEqual(web_app.verification_for_event('B',2),'HIGH_CONFIDENCE')
    def test_event_single_media(self): self.assertEqual(web_app.verification_for_event('B',1),'PENDING')
    def test_position_block_quote(self):
        r=web_app.position_size({'total_capital':100000,'current_invested':0,'current_theme_exposure':0,'current_stock_exposure':0,'entry_price':10,'stop_price':9,'score':80,'risk_profile':'balanced','quote_validation':'BLOCK','news_verification':'CONFIRMED','is_new_theme':False})
        self.assertFalse(r['allowed']);self.assertEqual(r['executable_amount'],0)
    def test_position_allowed(self):
        r=web_app.position_size({'total_capital':100000,'current_invested':0,'current_theme_exposure':0,'current_stock_exposure':0,'entry_price':10,'stop_price':9,'score':80,'risk_profile':'balanced','quote_validation':'OK','news_verification':'CONFIRMED','is_new_theme':True})
        self.assertTrue(r['allowed']);self.assertGreater(r['executable_shares'],0)
    def test_database_empty_no_fake_selection(self):
        with tempfile.TemporaryDirectory() as td:
            db=web_app.Database(Path(td)/'x.db')
            self.assertEqual(db.all('SELECT * FROM fund_managers'),[])
            self.assertEqual(db.all('SELECT * FROM stock_scores'),[])
            self.assertGreaterEqual(len(db.all('SELECT * FROM watchlist')),1)
    def test_static_exists(self):
        self.assertTrue((web_app.STATIC/'index.html').exists())
        self.assertTrue((web_app.STATIC/'app.js').exists())
    def test_parse_position_text(self):
        text='证券代码 证券名称 持仓 可用 成本价 最新价\n600519 贵州茅台 100 100 1500.25 1600.00\n000001 平安银行 1000 11.23 12.00'
        rows,warnings=web_app.parse_position_text(text)
        self.assertEqual([x['symbol'] for x in rows],['600519','000001'])
        self.assertEqual(rows[0]['quantity'],100)
        self.assertEqual(rows[0]['average_cost'],1500.25)
        self.assertEqual(rows[1]['average_cost'],11.23)
        self.assertIsInstance(warnings,list)
    def test_parse_position_text_with_market_value_column(self):
        text='代码 名称 持仓 市值 成本价 现价 盈亏\n600519 贵州茅台 100 117888 1500.25 1178.88 -32137'
        rows,warnings=web_app.parse_position_text(text)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['quantity'],100)
        self.assertEqual(rows[0]['average_cost'],1500.25)
    def test_parse_position_text_repairs_missing_decimal(self):
        text='600519 GuizhouMaotai 100 150025 117888'
        rows,warnings=web_app.parse_position_text(text)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['quantity'],100)
        self.assertEqual(rows[0]['average_cost'],1500.25)
    def test_parse_broker_row_text_repairs_code_and_cost(self):
        row = '002S32 天 山 铝 业 1000 1000 0 11，114'
        item = web_app.parse_broker_row_text(row)
        self.assertEqual(item['symbol'], '002532')
        self.assertEqual(item['name'], '天山铝业')
        self.assertEqual(item['quantity'], 1000)
        self.assertEqual(item['average_cost'], 11.114)
    def test_parse_broker_row_text_repairs_split_quantity(self):
        row = '68847S 萤 石 网 络 1 1 6 1 1 6 0 1 1.510'
        item = web_app.parse_broker_row_text(row)
        self.assertEqual(item['symbol'], '688475')
        self.assertEqual(item['name'], '萤石网络')
        self.assertEqual(item['quantity'], 116)
        self.assertEqual(item['average_cost'], 11.510)
    def test_parse_position_text_extracts_current_price_and_theme(self):
        text='证券代码 证券名称 持仓 成本价 最新价\n600036 招商银行 100 35.791 35.590'
        rows,warnings=web_app.parse_position_text(text)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['symbol'],'600036')
        self.assertEqual(rows[0]['current_price'],35.590)
        self.assertEqual(rows[0]['theme'],'金融-银行')
    def test_parse_gold_account_position(self):
        text='招行黄金账户 持仓金额(元) 6906.97 持仓克重=8.0002克，成本均价=1014.83元/克 实时买入价 868.35 实时卖出价 863.35'
        rows,warnings=web_app.parse_product_positions_text(text)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['asset_type'],'gold')
        self.assertEqual(rows[0]['theme'],'黄金/贵金属')
        self.assertAlmostEqual(rows[0]['quantity'],8.0002,places=4)
        self.assertEqual(rows[0]['current_price'],863.35)
    def test_parse_account_summary_text(self):
        text='资金余额 54709.39 可取金额 0.00 持仓盈亏 -2487.41 冻结金额 28055.98 股票市值 149713.32 当日盈亏 -961.28 可用金额 26653.41 总资产 204422.71 当日盈亏比 -0.47%'
        summary=web_app.parse_account_summary_text(text)
        self.assertEqual(summary['account_total_asset'],204422.71)
        self.assertEqual(summary['account_stock_market_value'],149713.32)
        self.assertAlmostEqual(summary['account_daily_pnl_pct'],-0.0047)
    def test_parse_fund_product_rows_without_codes(self):
        text='黄金产业 840.70 -520.30 -38.22% 700 700 1.944 1.201\n科技50 116.80 +57.60 +97.30% 100 100 0.592 1.168\n中证医疗 2534.00 -509.00 -16.78% 7000 7000 0.435 0.362'
        rows,warnings=web_app.parse_product_positions_text(text)
        self.assertEqual([x['name'] for x in rows], ['黄金产业','科技50','中证医疗'])
        self.assertTrue(all(x['asset_type']=='fund' for x in rows))
        self.assertEqual(rows[0]['theme'],'黄金/贵金属')
        self.assertEqual(rows[1]['theme'],'科技指数')
        self.assertEqual(rows[2]['theme'],'医药医疗')
        self.assertEqual(rows[2]['quantity'],7000)
        self.assertEqual(rows[2]['current_price'],0.362)
    def test_backfill_position_metadata_updates_old_screenshot_theme(self):
        web_app.DB.execute("DELETE FROM positions WHERE symbol='300750'")
        web_app.upsert_position({"symbol":"300750","name":"宁德时代","theme":"截图导入","asset_type":"stock","quantity":100,"average_cost":391.135,"score":50})
        result = web_app.backfill_position_metadata()
        row = web_app.DB.one("SELECT theme,asset_type FROM positions WHERE symbol=?", ("300750",))
        self.assertTrue(result["updated"] >= 1)
        self.assertEqual(row["theme"], "新能源-动力电池")
        self.assertEqual(row["asset_type"], "stock")
    def test_parse_rss_items(self):
        rss='<?xml version="1.0"?><rss><channel><item><title>600000 公司公告</title><link>https://example.com/a</link><pubDate>Mon, 29 Jun 2026 10:00:00 +0800</pubDate></item></channel></rss>'
        items=web_app.parse_rss_items(rss,{'name':'测试源','source_level':'A','source_root':'example'})
        self.assertEqual(len(items),1)
        self.assertEqual(items[0]['title'],'600000 公司公告')

    def test_market_signal_only_status(self):
        web_app.DB.execute("DELETE FROM fund_managers")
        web_app.DB.execute("DELETE FROM fund_consensus")
        web_app.DB.execute("DELETE FROM stock_scores")
        web_app.DB.execute(
            "INSERT OR REPLACE INTO stock_scores(symbol,name,industry,trend,risk,fund_signal,total_score,grade,data_date,source_status,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("300750", "宁德时代", "市场热度", 80, 45, 0, 76, "市场热度线索", web_app.now_iso(), "A_STOCK_DATA_SIGNAL", web_app.now_iso()),
        )
        data = web_app.selection_data()
        self.assertEqual(data["status"], "MARKET_SIGNAL_ONLY")
        self.assertIn("不是官方基金经理持仓推荐", data["message"])

    def test_import_official_fund_payload_builds_research(self):
        for table in ["fund_report_holdings", "fund_reports", "fund_products", "fund_managers", "fund_consensus", "stock_scores", "stock_due_diligence", "stock_valuations"]:
            web_app.DB.execute("DELETE FROM " + table)
        payload = {
            "managers": [{"manager_id": "m1", "name": "测试经理", "company": "测试基金", "score": 82, "tenure_years": 5, "report_period": "2026Q1", "evidence_status": "VERIFIED"}],
            "products": [{"fund_code": "000001", "name": "测试基金A", "company": "测试基金", "manager_id": "m1", "manager_name": "测试经理", "evidence_status": "VERIFIED"}],
            "reports": [{"report_id": "r1", "fund_code": "000001", "fund_name": "测试基金A", "manager_id": "m1", "manager_name": "测试经理", "company": "测试基金", "report_period": "2026Q1", "evidence_status": "VERIFIED"}],
            "holdings": [{"report_id": "r1", "symbol": "600000", "name": "浦发银行", "industry": "银行", "change_shares": 1000, "evidence_status": "VERIFIED"}],
        }
        imported = web_app.import_official_fund_payload(payload)
        self.assertEqual(imported["holdings"], 1)
        research = web_app.fund_research_data()
        self.assertEqual(research["status"], "READY")
        self.assertEqual(research["stocks"][0]["symbol"], "600000")
        self.assertIn("trade_plan", research["stocks"][0])

    def test_valuation_import_preserves_fundamental_due_diligence(self):
        for table in ["fund_report_holdings", "fund_reports", "fund_products", "fund_managers", "fund_consensus", "stock_scores", "stock_due_diligence", "stock_valuations"]:
            web_app.DB.execute("DELETE FROM " + table)
        web_app.import_stock_due_diligence_items([{
            "symbol": "600000",
            "name": "浦发银行",
            "profit_trend": 72,
            "cashflow_quality": 70,
            "debt_risk": 35,
            "industry_outlook": 68,
            "competitive_position": 66,
            "evidence_status": "VERIFIED",
        }])
        web_app.upsert_stock_valuations([{
            "symbol": "600000",
            "name": "浦发银行",
            "pe_ttm": 6.5,
            "pb": 0.55,
            "price_percentile": 20,
            "valuation_percentile": 12,
            "price_drawdown_pct": -22,
            "post_disclosure_runup_pct": 3,
            "lookback_days": 1095,
            "source": "测试估值",
            "source_url": "https://example.test/600000",
            "evidence_status": "PARTIAL_VALUATION_PROXY",
        }])
        row = web_app.DB.one("SELECT * FROM stock_due_diligence WHERE symbol=?", ("600000",))
        self.assertEqual(row["profit_trend"], 72)
        self.assertEqual(row["valuation_percentile"], 12)
        self.assertEqual(web_app.DB.one("SELECT * FROM stock_valuations WHERE symbol=?", ("600000",))["pb"], 0.55)

    def test_low_valuation_does_not_override_missing_fundamental_gate(self):
        for table in ["fund_report_holdings", "fund_reports", "fund_products", "fund_managers", "fund_consensus", "stock_scores", "stock_due_diligence", "stock_valuations"]:
            web_app.DB.execute("DELETE FROM " + table)
        payload = {
            "managers": [
                {"manager_id": "m1", "name": "甲经理", "company": "甲基金", "score": 82, "tenure_years": 5, "report_period": "2026Q1", "evidence_status": "VERIFIED"},
                {"manager_id": "m2", "name": "乙经理", "company": "乙基金", "score": 80, "tenure_years": 6, "report_period": "2026Q1", "evidence_status": "VERIFIED"},
            ],
            "products": [
                {"fund_code": "000001", "name": "测试基金A", "company": "甲基金", "manager_id": "m1", "manager_name": "甲经理", "strategy_track": "S1", "evidence_status": "VERIFIED"},
                {"fund_code": "000002", "name": "测试基金B", "company": "乙基金", "manager_id": "m2", "manager_name": "乙经理", "strategy_track": "S2", "evidence_status": "VERIFIED"},
            ],
            "reports": [
                {"report_id": "r0a", "fund_code": "000001", "fund_name": "测试基金A", "manager_id": "m1", "manager_name": "甲经理", "company": "甲基金", "report_period": "2025Q4", "evidence_status": "VERIFIED"},
                {"report_id": "r0b", "fund_code": "000002", "fund_name": "测试基金B", "manager_id": "m2", "manager_name": "乙经理", "company": "乙基金", "report_period": "2025Q4", "evidence_status": "VERIFIED"},
                {"report_id": "r1a", "fund_code": "000001", "fund_name": "测试基金A", "manager_id": "m1", "manager_name": "甲经理", "company": "甲基金", "report_period": "2026Q1", "evidence_status": "VERIFIED"},
                {"report_id": "r1b", "fund_code": "000002", "fund_name": "测试基金B", "manager_id": "m2", "manager_name": "乙经理", "company": "乙基金", "report_period": "2026Q1", "evidence_status": "VERIFIED"},
            ],
            "holdings": [
                {"report_id": "r0a", "symbol": "600000", "name": "浦发银行", "change_shares": 100, "evidence_status": "VERIFIED"},
                {"report_id": "r0b", "symbol": "600000", "name": "浦发银行", "change_shares": 100, "evidence_status": "VERIFIED"},
                {"report_id": "r1a", "symbol": "600000", "name": "浦发银行", "change_shares": 100, "evidence_status": "VERIFIED"},
                {"report_id": "r1b", "symbol": "600000", "name": "浦发银行", "change_shares": 100, "evidence_status": "VERIFIED"},
            ],
        }
        web_app.import_official_fund_payload(payload)
        web_app.upsert_stock_valuations([{
            "symbol": "600000",
            "name": "浦发银行",
            "pe_ttm": 6.5,
            "pb": 0.55,
            "price_percentile": 18,
            "valuation_percentile": 10,
            "price_drawdown_pct": -25,
            "post_disclosure_runup_pct": 2,
            "lookback_days": 1095,
            "source": "测试估值",
            "source_url": "https://example.test/600000",
            "evidence_status": "PARTIAL_VALUATION_PROXY",
        }])
        score = web_app.DB.one("SELECT * FROM stock_scores WHERE symbol=?", ("600000",))
        self.assertEqual(score["triple_confirm_status"], "RESEARCH_ONLY_NEEDS_FUNDAMENTAL_VALUATION")
        self.assertLessEqual(score["total_score"], 68)
        self.assertIn("观察", score["grade"])

    def test_position_action_distinguishes_break_from_washout(self):
        for table in ["positions", "quote_validation", "stock_scores", "stock_due_diligence", "stock_valuations", "news_events"]:
            web_app.DB.execute("DELETE FROM " + table)
        web_app.upsert_position({"symbol":"600000","name":"浦发银行","theme":"银行","quantity":100,"average_cost":10,"stop_price":9,"score":75})
        web_app.DB.execute(
            "INSERT OR REPLACE INTO quote_validation(symbol,name,last_price,level,primary_provider,secondary_provider,deviation,reasons,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            ("600000","浦发银行",9.5,"OK","tencent","mootdx",0,"[]",web_app.now_iso()),
        )
        web_app.DB.execute(
            "INSERT OR REPLACE INTO stock_scores(symbol,name,total_score,valuation_signal,triple_confirm_status,exclusion_flags,source_status,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            ("600000","浦发银行",72,82,"EXCLUDED",'["利润连续下滑或盈利趋势破坏"]',"OFFICIAL_FUND_REPORT",web_app.now_iso()),
        )
        action = web_app.portfolio()["positions"][0]["position_action"]
        self.assertEqual(action["diagnosis"], "基本盘风险")
        web_app.DB.execute(
            "INSERT OR REPLACE INTO stock_scores(symbol,name,total_score,valuation_signal,triple_confirm_status,exclusion_flags,source_status,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            ("600000","浦发银行",72,82,"WATCH_ONLY","[]","OFFICIAL_FUND_REPORT",web_app.now_iso()),
        )
        web_app.DB.execute(
            "INSERT OR REPLACE INTO stock_valuations(symbol,name,pe_ttm,pb,valuation_percentile,price_drawdown_pct,evidence_status,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            ("600000","浦发银行",6,0.6,20,-18,"PARTIAL_VALUATION_PROXY",web_app.now_iso()),
        )
        action = web_app.portfolio()["positions"][0]["position_action"]
        self.assertEqual(action["action"], "右侧加仓观察")
        plan = web_app.portfolio()["positions"][0]["daily_trade_plan"]
        self.assertEqual(plan["mode"], "DAILY_RULE_ANALYSIS")
        self.assertGreater(plan["stop_loss"], 0)

    def test_portfolio_account_summary_recalculates_weight(self):
        user_id = "acct-test"
        web_app.DB.execute("DELETE FROM positions WHERE owner_id=?", (user_id,))
        web_app.DB.execute("DELETE FROM user_settings WHERE owner_id=?", (user_id,))
        web_app.upsert_position({"symbol":"002532","name":"天山铝业","theme":"有色金属","quantity":1000,"average_cost":11.114,"current_price":10.68,"score":50}, user_id)
        web_app.DB.set_settings({
            "account_cash_balance": 54709.39,
            "account_stock_market_value": 149713.32,
            "account_total_asset": 204422.71,
            "account_daily_pnl": -961.28,
            "account_daily_pnl_pct": -0.0047,
        }, user_id)
        p = web_app.portfolio(user_id)
        self.assertEqual(p["capital_base"], 204422.71)
        self.assertAlmostEqual(p["positions"][0]["portfolio_weight"], 10680/204422.71)
        self.assertAlmostEqual(p["stock_invested_weight"], 149713.32/204422.71)

    def test_stock_consultation_proxy_score_and_chain(self):
        for table in ["quote_validation", "stock_scores", "stock_valuations", "fund_consensus", "stock_due_diligence", "fund_report_holdings", "fund_reports"]:
            web_app.DB.execute("DELETE FROM " + table)
        web_app.DB.execute(
            "INSERT OR REPLACE INTO quote_validation(symbol,name,last_price,level,primary_provider,secondary_provider,deviation,reasons,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            ("300750","宁德时代",395.0,"WARN",None,"tencent",None,'["仅有单一新鲜行情源，禁止强买入信号"]',web_app.now_iso()),
        )
        web_app.DB.execute(
            "INSERT OR REPLACE INTO stock_valuations(symbol,name,pe_ttm,pb,valuation_percentile,price_drawdown_pct,evidence_status,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            ("300750","宁德时代",28,4.2,22,-18,"PARTIAL_VALUATION_PROXY",web_app.now_iso()),
        )
        result = web_app.stock_consultation_data("300750", refresh=False)
        self.assertTrue(result["ok"])
        self.assertEqual(result["symbol"], "300750")
        self.assertEqual(result["score"]["source_status"], "QUOTE_VALUATION_PROXY")
        self.assertGreater(result["score"]["total_score"], 50)
        self.assertIn("回撤观察区", result["trade_plan"]["entry"])
        self.assertEqual(result["industry_chain"]["chain"], "新能源车")

    def test_stock_consultation_accepts_000_stock_code(self):
        web_app.DB.execute("DELETE FROM quote_validation WHERE symbol='000001'")
        web_app.DB.execute(
            "INSERT OR REPLACE INTO quote_validation(symbol,name,last_price,level,primary_provider,secondary_provider,deviation,reasons,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            ("000001","平安银行",12.34,"WARN",None,"tencent",None,"[]",web_app.now_iso()),
        )
        result = web_app.stock_consultation_data("000001", refresh=False)
        self.assertTrue(result["ok"])
        self.assertEqual(result["symbol"], "000001")

    def test_send_whatsapp_notification(self):
        captured = {}
        globals_map = web_app.send_notification.__globals__
        old_post = globals_map["post_json"]
        try:
            web_app.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            web_app.CONFIG_PATH.write_text(json.dumps({
                "notification": {
                    "whatsapp_access_token": "token-1",
                    "whatsapp_phone_number_id": "12345",
                    "whatsapp_to": "15551234567",
                    "whatsapp_graph_api_version": "v23.0",
                }
            }), encoding="utf-8")
            def fake_post(url, payload, headers=None, timeout=8.0):
                captured["url"] = url
                captured["payload"] = payload
                captured["headers"] = headers
                return 200, "{}"
            globals_map["post_json"] = fake_post
            result = web_app.send_notification("测试消息", "whatsapp")
            self.assertEqual(result["results"][0]["status"], "OK")
            self.assertIn("/v23.0/12345/messages", captured["url"])
            self.assertEqual(captured["payload"]["messaging_product"], "whatsapp")
            self.assertEqual(captured["headers"]["Authorization"], "Bearer token-1")
        finally:
            globals_map["post_json"] = old_post
            try:
                web_app.CONFIG_PATH.unlink()
            except FileNotFoundError:
                pass

    def test_build_position_candidates_low_valuation(self):
        for table in ["positions", "stock_scores", "stock_valuations"]:
            web_app.DB.execute("DELETE FROM " + table)
        web_app.DB.execute(
            "INSERT OR REPLACE INTO stock_scores(symbol,name,total_score,valuation_signal,triple_confirm_status,source_status,updated_at) VALUES(?,?,?,?,?,?,?)",
            ("600000","浦发银行",70,85,"WATCH_ONLY","OFFICIAL_FUND_REPORT",web_app.now_iso()),
        )
        web_app.DB.execute(
            "INSERT OR REPLACE INTO stock_valuations(symbol,name,pe_ttm,pb,valuation_percentile,price_drawdown_pct,evidence_status,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            ("600000","浦发银行",6,0.6,18,-22,"PARTIAL_VALUATION_PROXY",web_app.now_iso()),
        )
        candidates = web_app.build_position_candidates()
        self.assertEqual(candidates[0]["symbol"], "600000")
        self.assertIn("低估值", candidates[0]["action"])

    def test_parse_eid_fund_detail_html(self):
        html = """
        <td id="sp_fundName" value=测试基金>测试基金全称</td>
        <td>基金类别</td><td>混合型</td>
        <td>基金管理人</td><td>测试基金管理有限公司</td>
        <td>基金托管人</td><td>测试银行</td>
        <td>基金合同生效日期</td><td>2020-01-01</td>
        """
        parsed = parse_fund_detail_html(html)
        self.assertEqual(parsed["name"], "测试基金全称")
        self.assertEqual(parsed["company"], "测试基金管理有限公司")
        self.assertEqual(parsed["category"], "混合型")

    def test_parse_eid_holding_lines(self):
        text = """序号 股票代码 股票名称 数量（股） 公允价值（元） 占基金资产净值比例（%）
1 688376 美辰科技 112,800 6,553,680.00 4.15
2 300750 宁德时代 15,600 6,266,520.00 3.97
"""
        rows = parse_stock_holdings_from_text(text, "PDF_PAGE_8")
        self.assertEqual([x["symbol"] for x in rows], ["688376", "300750"])
        self.assertEqual(rows[0]["holding_rank"], 1)
        self.assertEqual(rows[0]["shares"], 112800)
        self.assertEqual(rows[0]["market_value"], 6553680)
        self.assertEqual(rows[0]["nav_ratio"], 4.15)
        self.assertEqual(rows[0]["source_page"], "PDF_PAGE_8")

    def test_eid_report_period(self):
        row = {"reportYear": "2026", "reportDesp": "第一季度报告", "reportCode": "FB030010"}
        self.assertEqual(report_period_from_row(row), "2026Q1")

    def test_parse_eid_fund_manager_rows_filters_departed(self):
        rows = [
            ["姓名", "职务", "任职日期", "离任日期", "证券从业年限"],
            ["卞美莹", "本基金基金经理", "2026年1月15日", "-", "七年"],
            ["陈启明", "本基金基金经理", "2014年9月26日", "2026年1月26日", "二十年"],
        ]
        managers = parse_fund_manager_rows(rows, company="华富基金管理有限公司", report_period="2026Q1", source_url="https://example.test/report.pdf")
        self.assertEqual([x["name"] for x in managers], ["卞美莹"])
        self.assertGreater(managers[0]["score"], 60)
        self.assertEqual(managers[0]["evidence_status"], "VERIFIED")

if __name__=='__main__':unittest.main()
