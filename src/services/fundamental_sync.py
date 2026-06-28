# -*- coding: utf-8 -*-
"""
===================================
基本面数据同步服务
===================================

职责：
1. 从 akshare (Sina/THS) 拉取全A股基本面数据
2. 批量写入本地 SQLite 数据库
3. 支持全量同步和增量更新

设计原则：
- 直接调用 akshare（不通过 DataFetcherManager 的 fallback 链），因为这是批量全量场景
- THS 源为主要财务数据源（East Money 源被封锁时 THS 仍可用）
- 内置限速避免 IP 被封
"""

import logging
import re
import time
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

import akshare as ak
import pandas as pd
import requests

from src.repositories.fundamental_repo import FundamentalRepository

logger = logging.getLogger(__name__)

# 列名映射：THS stock_financial_abstract_ths() 输出列 → FinancialAbstract ORM 字段
_THS_COLUMN_MAP: Dict[str, str] = {
    '净利润': 'net_profit',
    '净利润同比增长率': 'net_profit_yoy',
    '扣非净利润': 'net_profit_deducted',
    '营业总收入': 'revenue',
    '营业总收入同比增长率': 'revenue_yoy',
    '基本每股收益': 'eps',
    '每股净资产': 'bvps',
    '每股资本公积金': 'capital_reserve_ps',
    '每股未分配利润': 'retained_earnings_ps',
    '每股经营现金流': 'operating_cf_ps',
    '销售净利率': 'net_margin',
    '销售毛利率': 'gross_margin',
    '净资产收益率': 'roe',
    '净资产收益率-摊薄': 'roe_diluted',
    '营业周期': 'operating_cycle',
    '存货周转率': 'inventory_turnover',
    '应收账款周转天数': 'receivables_turnover_days',
    '流动比率': 'current_ratio',
    '速动比率': 'quick_ratio',
    '资产负债率': 'debt_ratio',
}


def _parse_ths_value(raw: Any) -> Optional[float]:
    """解析 THS 数据源中的值。

    处理格式：
    - "514.43亿" → 51443000000.0
    - "1.47亿" → 147000000.0
    - "2.16万" → 21600.0
    - "46.84%" → 46.84
    - "1.1700" → 1.17
    - Python False / None → None
    """
    if raw is None:
        return None
    if raw is False:
        return None
    if raw is True:
        return None
    if isinstance(raw, (int, float)):
        if pd.isna(raw):
            return None
        return float(raw)

    s = str(raw).strip()
    if not s:
        return None

    try:
        # 百分比
        if s.endswith('%'):
            return float(s[:-1])

        # 亿 (100 million)
        if s.endswith('亿'):
            num = float(s[:-1])
            return num * 100_000_000

        # 万 (10 thousand)
        if s.endswith('万'):
            num = float(s[:-1])
            return num * 10_000

        # 纯数字
        return float(s)
    except (ValueError, TypeError):
        logger.debug(f"Cannot parse THS value: {raw!r}")
        return None


def _derive_market(code: str) -> str:
    """根据股票代码推断市场。

    Args:
        code: 纯数字代码，如 "600519"

    Returns:
        "SH" / "SZ" / "BJ"
    """
    if code.startswith('6'):
        return 'SH'
    if code.startswith(('0', '3')):
        return 'SZ'
    if code.startswith(('8', '4', '9')):
        return 'BJ'
    return 'UNKNOWN'


def _code_to_akshare_symbol(code: str) -> str:
    """将纯数字代码转为 akshare THS 接口需要的格式（纯数字即可）。"""
    return code


def _normalize_industry_label(name: str) -> str:
    """规范化交易所/行情源返回的行业名称。"""
    text = (name or "").strip()
    if not text or text in {"-", "--", "nan", "None"}:
        return ""
    # 证监会行业常见格式：'J 金融业' → '金融业'
    if len(text) > 2 and text[1] == " " and text[0].isalpha():
        return text[2:].strip()
    return text


def _em_market_code(code: str) -> int:
    """东财行情接口 secid 市场段：沪市=1，其余=0。"""
    return 1 if code.startswith("6") else 0


# ========================================================================
# FundamentalSyncService
# ========================================================================


class FundamentalSyncService:
    """基本面数据同步服务。

    编排全量/增量同步，协调 akshare API 调用和数据库写入。

    Usage:
        svc = FundamentalSyncService()
        svc.full_sync()               # 全量同步所有股票
        svc.sync_stocks(['600519'])   # 同步指定股票
    """

    def __init__(self, repo: Optional[FundamentalRepository] = None):
        """初始化同步服务。

        Args:
            repo: 可选，FundamentalRepository 实例（默认自动创建）
        """
        self.repo = repo or FundamentalRepository()
        self._rate_limit: float = 0.3  # API 调用间的最小间隔（秒）
        self._last_call: float = 0.0

    def _upsert_industry_map(self, code_to_industry: Dict[str, str]) -> int:
        """批量写入行业映射，返回成功条数。"""
        count = 0
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for code, industry in code_to_industry.items():
            if not industry:
                continue
            success = self.repo.upsert_stock_listing({
                'code': code,
                'industry_ths': industry,
                'updated_at': now,
            })
            if success:
                count += 1
        return count

    def _fetch_industry_from_em_quote(self, code: str) -> str:
        """从东财个股行情接口读取行业字段 f127。"""
        try:
            response = requests.get(
                "https://push2.eastmoney.com/api/qt/stock/get",
                params={
                    "fltt": "2",
                    "invt": "2",
                    "fields": "f127",
                    "secid": f"{_em_market_code(code)}.{code}",
                },
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json().get("data") or {}
            return _normalize_industry_label(str(payload.get("f127") or ""))
        except Exception as exc:
            logger.debug("EM quote industry fetch failed for %s: %s", code, exc)
            return ""

    def _throttle(self) -> None:
        """API 限速：确保两次调用之间至少间隔 _rate_limit 秒。"""
        now = time.time()
        elapsed = now - self._last_call
        if elapsed < self._rate_limit:
            time.sleep(self._rate_limit - elapsed)
        self._last_call = time.time()

    # ====================================================================
    # 股票清单同步
    # ====================================================================

    def sync_stock_list(self) -> int:
        """全量同步 A 股股票清单。

        数据源：Sina (stock_info_a_code_name)。
        返回：写入的股票数量。
        """
        logger.info("Starting stock list sync from Sina...")
        try:
            self._throttle()
            df = ak.stock_info_a_code_name()
            logger.info(f"Fetched {len(df)} stocks from Sina")
        except Exception as e:
            logger.error(f"Failed to fetch stock list from Sina: {e}")
            return 0

        records: List[Dict[str, Any]] = []
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for _, row in df.iterrows():
            code = str(row['code']).strip()
            name = str(row['name']).strip()
            market = _derive_market(code)
            records.append({
                'code': code,
                'name': name,
                'market': market,
                'status': 'listed',
                'updated_at': now,
            })

        return self.repo.upsert_stock_listings_batch(records)

    def repair_missing_stock_names(self) -> int:
        """从新浪全 A 股清单补全数据库中缺失的股票名称。"""
        codes_needed = set(self.repo.get_stocks_without_name())
        if not codes_needed:
            logger.info("All listed stocks already have names, skipping repair.")
            return 0

        logger.info("Repairing missing stock names for %s codes...", len(codes_needed))
        try:
            self._throttle()
            df = ak.stock_info_a_code_name()
        except Exception as exc:
            logger.error("Failed to fetch Sina stock list for name repair: %s", exc)
            return 0

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        count = 0
        for _, row in df.iterrows():
            code = str(row["code"]).strip().zfill(6)
            if code not in codes_needed:
                continue
            name = str(row["name"]).strip()
            if not name:
                continue
            if self.repo.upsert_stock_listing({
                "code": code,
                "name": name,
                "market": _derive_market(code),
                "updated_at": now,
            }):
                count += 1

        logger.info("Repaired stock names for %s codes", count)
        return count

    def enrich_industry_from_ths(self) -> int:
        """补充 stock_listing.industry_ths 行业分类。

        行业板块列表与成分股均来自东财（AkShare EM 接口）；字段名保留
        industry_ths 以兼容现有 API 与页面。仅对缺失行业的股票进行补充。

        返回：补充了行业的股票数量。
        """
        codes_needed = set(self.repo.get_stocks_without_industry())
        if not codes_needed:
            logger.info("All stocks already have industry classification, skipping.")
            return 0

        logger.info(
            "Fetching EM industry boards for %s stocks without industry...",
            len(codes_needed),
        )

        code_to_industry: Dict[str, str] = {}

        boards = None
        last_err: Optional[Exception] = None
        for attempt in range(1, 6):
            try:
                self._throttle()
                boards = ak.stock_board_industry_name_em()
                logger.info(
                    "Got %s EM industry boards (attempt %s)",
                    len(boards),
                    attempt,
                )
                break
            except Exception as e:
                last_err = e
                logger.warning(
                    "EM industry boards attempt %s failed: %s",
                    attempt,
                    e,
                )
                time.sleep(min(2 * attempt, 10))

        if boards is None:
            logger.error("Failed to fetch EM industry boards: %s", last_err)
            return 0

        for idx, board_row in boards.iterrows():
            board_name = str(board_row["板块名称"]).strip()
            board_code = str(board_row["板块代码"]).strip()
            try:
                self._throttle()
                cons = ak.stock_board_industry_cons_em(symbol=board_code)
                for _, cons_row in cons.iterrows():
                    stock_code = str(cons_row["代码"]).strip()
                    stock_code = re.sub(r"^(SH|SZ|BJ)", "", stock_code)
                    if stock_code in codes_needed:
                        code_to_industry[stock_code] = board_name
                if (idx + 1) % 20 == 0:
                    logger.info(
                        "Industry enrich progress: %s/%s boards, %s stocks mapped",
                        idx + 1,
                        len(boards),
                        len(code_to_industry),
                    )
            except Exception as e:
                logger.warning(
                    "Failed to fetch constituents for board '%s' (%s): %s",
                    board_name,
                    board_code,
                    e,
                )
                continue

            if len(code_to_industry) >= len(codes_needed):
                logger.info("All stocks mapped to industries, stopping early")
                break

        # 写入
        count = self._upsert_industry_map(code_to_industry)

        logger.info(f"Enriched industry for {count} stocks")
        return count

    def enrich_industry_from_exchange(self) -> int:
        """从沪深北交易所股票列表补充证监会行业分类。"""
        codes_needed = set(self.repo.get_stocks_without_industry())
        if not codes_needed:
            logger.info("All stocks already have industry classification, skipping exchange.")
            return 0

        logger.info(
            "Fetching exchange industry listings for %s stocks without industry...",
            len(codes_needed),
        )
        code_to_industry: Dict[str, str] = {}

        try:
            sz_df = None
            last_sz_err: Optional[Exception] = None
            for attempt in range(1, 6):
                try:
                    self._throttle()
                    sz_df = ak.stock_info_sz_name_code(symbol="A股列表")
                    logger.info(
                        "Fetched SZ exchange listing on attempt %s (%s rows)",
                        attempt,
                        len(sz_df),
                    )
                    break
                except Exception as exc:
                    last_sz_err = exc
                    logger.warning(
                        "SZ exchange industry attempt %s failed: %s",
                        attempt,
                        exc,
                    )
                    time.sleep(min(2 * attempt, 10))
            if sz_df is None:
                raise last_sz_err or RuntimeError("SZ exchange listing unavailable")

            for _, row in sz_df.iterrows():
                code = str(row["A股代码"]).strip().zfill(6)
                industry = _normalize_industry_label(str(row.get("所属行业", "")))
                if code in codes_needed and industry:
                    code_to_industry[code] = industry
            logger.info("SZ exchange listing mapped %s stocks", len(code_to_industry))
        except Exception as exc:
            logger.warning("Failed to fetch SZ exchange industry listing: %s", exc)

        try:
            self._throttle()
            bj_df = ak.stock_info_bj_name_code()
            before = len(code_to_industry)
            for _, row in bj_df.iterrows():
                code = str(row["证券代码"]).strip().zfill(6)
                industry = _normalize_industry_label(str(row.get("所属行业", "")))
                if code in codes_needed and industry:
                    code_to_industry[code] = industry
            logger.info(
                "BJ exchange listing mapped %s additional stocks",
                len(code_to_industry) - before,
            )
        except Exception as exc:
            logger.warning("Failed to fetch BJ exchange industry listing: %s", exc)

        count = self._upsert_industry_map(code_to_industry)
        logger.info("Exchange industry enrich wrote %s stocks", count)
        return count

    def enrich_industry_from_em_quote(self, codes: Optional[List[str]] = None) -> int:
        """对仍缺行业的股票，逐只查询东财行情行业字段。"""
        target_codes = list(codes or self.repo.get_stocks_without_industry())
        if not target_codes:
            logger.info("All stocks already have industry classification, skipping EM quote.")
            return 0

        logger.info(
            "Fetching EM quote industry for %s stocks without industry...",
            len(target_codes),
        )
        count = 0
        for index, code in enumerate(target_codes):
            if index > 0:
                self._throttle()
            industry = self._fetch_industry_from_em_quote(code)
            if not industry:
                continue
            if self._upsert_industry_map({code: industry}):
                count += 1
            if (index + 1) % 100 == 0:
                logger.info(
                    "EM quote industry progress: %s/%s, enriched=%s",
                    index + 1,
                    len(target_codes),
                    count,
                )

        logger.info("EM quote industry enrich wrote %s stocks", count)
        return count

    def enrich_industry_all(self) -> Dict[str, int]:
        """按速度由快到慢依次补全行业：交易所 → 东财个股 → 东财板块成分。"""
        result = {
            "exchange": self.enrich_industry_from_exchange(),
            "quote": self.enrich_industry_from_em_quote(),
        }
        remaining = len(self.repo.get_stocks_without_industry())
        if remaining > 0:
            logger.info(
                "%s stocks still without industry after exchange + quote; "
                "skipping slow EM board scan (re-run with --boards to enable).",
                remaining,
            )
            result["boards"] = 0
        result["remaining"] = len(self.repo.get_stocks_without_industry())
        result["names_repaired"] = self.repair_missing_stock_names()
        logger.info("Industry enrich all complete: %s", result)
        return result

    # ====================================================================
    # 财务数据同步
    # ====================================================================

    def sync_financials_for_stock(self, code: str) -> int:
        """同步单只股票的财务指标摘要（THS 源）。

        Args:
            code: 股票代码（纯数字，如 "600519"）

        Returns:
            写入的记录数（多少个报告期）
        """
        try:
            self._throttle()
            df = ak.stock_financial_abstract_ths(symbol=code)
        except Exception as e:
            logger.error(f"Failed to fetch financial abstract for {code}: {e}")
            return 0

        if df is None or df.empty:
            logger.debug(f"No financial data for {code}")
            return 0

        records: List[Dict[str, Any]] = []
        for _, row in df.iterrows():
            report_period_str = str(row.iloc[0]).strip()
            try:
                report_period = datetime.strptime(report_period_str, '%Y-%m-%d').date()
            except ValueError:
                logger.debug(f"Cannot parse report period '{report_period_str}' for {code}")
                continue

            record: Dict[str, Any] = {
                'code': code,
                'report_period': report_period,
                'source': 'THS',
            }

            # 按列名映射提取指标
            for ths_col, orm_field in _THS_COLUMN_MAP.items():
                if ths_col in df.columns:
                    record[orm_field] = _parse_ths_value(row[ths_col])

            records.append(record)

        if records:
            return self.repo.upsert_financials_batch(records)
        return 0

    def sync_financials_batch(
        self,
        codes: List[str],
        pause_every: int = 50,
        pause_seconds: float = 3.0,
    ) -> Dict[str, Any]:
        """批量同步多只股票的财务指标。

        Args:
            codes: 股票代码列表
            pause_every: 每 N 只股票暂停一次
            pause_seconds: 暂停秒数

        Returns:
            统计字典: {total_stocks, synced, failed, total_records}
        """
        total = len(codes)
        synced = 0
        failed = 0
        total_records = 0

        logger.info(f"Starting batch financial sync for {total} stocks")
        start_time = time.time()

        for i, code in enumerate(codes):
            try:
                records = self.sync_financials_for_stock(code)
                if records > 0:
                    synced += 1
                    total_records += records
                else:
                    failed += 1
            except Exception as e:
                logger.warning(f"Sync financials for {code} failed: {e}")
                failed += 1

            # 进度日志 + 暂停
            if (i + 1) % 50 == 0 or (i + 1) == total:
                elapsed = time.time() - start_time
                rate = (i + 1) / elapsed if elapsed > 0 else 0
                logger.info(
                    f"Financial sync progress: {i + 1}/{total} "
                    f"({synced} synced, {failed} failed, {rate:.1f} stocks/s)"
                )

            if (i + 1) % pause_every == 0 and (i + 1) < total:
                time.sleep(pause_seconds)

        logger.info(
            f"Financial sync done: {total} stocks, {synced} synced, "
            f"{failed} failed, {total_records} records, "
            f"{time.time() - start_time:.0f}s elapsed"
        )

        return {
            'total_stocks': total,
            'synced': synced,
            'failed': failed,
            'total_records': total_records,
        }

    # ====================================================================
    # 估值快照同步
    # ====================================================================

    def sync_valuation_for_stock(self, code: str) -> int:
        """同步单只股票的估值快照（百度估值源）。

        Args:
            code: 股票代码（纯数字，如 "600519"）

        Returns:
            写入的记录数
        """
        try:
            self._throttle()
            df = ak.stock_zh_valuation_baidu(symbol=code)
        except Exception as e:
            logger.error(f"Failed to fetch valuation for {code}: {e}")
            return 0

        if df is None or df.empty:
            logger.debug(f"No valuation data for {code}")
            return 0

        records: List[Dict[str, Any]] = []
        for _, row in df.iterrows():
            try:
                trade_date = datetime.strptime(str(row['date']).strip(), '%Y-%m-%d').date()
                val = row.get('value')
                if val is None or (isinstance(val, float) and pd.isna(val)):
                    continue
                records.append({
                    'code': code,
                    'trade_date': trade_date,
                    'val_market_cap': float(val),
                })
            except (ValueError, KeyError) as e:
                logger.debug(f"Cannot parse valuation row for {code}: {e}")
                continue

        if records:
            count = 0
            for record in records:
                if self.repo.upsert_valuation(record):
                    count += 1
            return count
        return 0

    # ====================================================================
    # 全量编排
    # ====================================================================

    def full_sync(
        self,
        stocks: Optional[List[str]] = None,
        include_financials: bool = True,
        include_valuation: bool = False,
        include_industry_enrich: bool = False,
    ) -> Dict[str, Any]:
        """全量同步编排。

        执行顺序：股票清单 → 行业补充(可选) → 财务数据 → 估值快照(可选)

        Args:
            stocks: 指定股票代码列表（None = 全量）
            include_financials: 是否同步财务数据
            include_valuation: 是否同步估值快照（较慢，默认关闭）
            include_industry_enrich: 是否补充行业分类（较慢，默认关闭）

        Returns:
            各阶段统计字典
        """
        result: Dict[str, Any] = {
            'stock_list_count': 0,
            'industry_enriched': 0,
            'financials': {},
            'valuation_count': 0,
        }

        # Step 1: 股票清单
        if stocks:
            logger.info(f"Using provided stock list: {len(stocks)} stocks")
            codes = stocks
        else:
            logger.info("=== Phase 1: Stock List Sync ===")
            stock_count = self.sync_stock_list()
            result['stock_list_count'] = stock_count
            if stock_count == 0:
                logger.error("Stock list sync failed, aborting")
                return result
            codes = [s.code for s in self.repo.get_all_listed_stocks()]
            logger.info(f"Stock list sync done: {len(codes)} stocks in DB")

        # Step 2: 行业补充（可选）
        if include_industry_enrich:
            logger.info("=== Phase 2: Industry Enrich ===")
            enriched = self.enrich_industry_from_ths()
            result['industry_enriched'] = enriched
            logger.info(f"Industry enrich done: {enriched} stocks updated")

        # Step 3: 财务数据
        if include_financials:
            logger.info("=== Phase 3: Financial Data Sync ===")
            fin_result = self.sync_financials_batch(codes)
            result['financials'] = fin_result

        # Step 4: 估值快照（可选，较慢）
        if include_valuation:
            logger.info("=== Phase 4: Valuation Sync ===")
            val_count = 0
            for i, code in enumerate(codes[:100]):  # 限制 100 只避免过慢
                val_count += self.sync_valuation_for_stock(code)
                if (i + 1) % 20 == 0:
                    logger.info(f"Valuation progress: {i + 1}/{min(len(codes), 100)}")
            result['valuation_count'] = val_count

        logger.info(f"Full sync complete: {result}")
        return result
