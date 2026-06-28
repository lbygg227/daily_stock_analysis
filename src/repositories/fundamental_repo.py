# -*- coding: utf-8 -*-
"""
===================================
基本面数据访问层
===================================

职责：
1. 封装股票清单、财务摘要、估值快照的数据库操作
2. 提供批量 upsert 和查询接口
"""

import logging
from datetime import date
from typing import Optional, List, Dict, Any, Tuple

from sqlalchemy import and_, desc, or_, select, func
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.storage import (
    DatabaseManager,
    StockListing,
    FinancialAbstract,
    ValuationDaily,
)

logger = logging.getLogger(__name__)

UNCLASSIFIED_INDUSTRY_KEY = "__UNCLASSIFIED__"
_SORTABLE_FIELDS = {
    "code": StockListing.code,
    "roe": FinancialAbstract.roe,
    "revenue": FinancialAbstract.revenue,
    "revenue_yoy": FinancialAbstract.revenue_yoy,
    "net_margin": FinancialAbstract.net_margin,
}


def _listing_conflict_updates(record: Dict[str, Any]) -> Dict[str, Any]:
    """Build partial UPDATE set: only overwrite fields explicitly provided."""
    updates: Dict[str, Any] = {}
    if record.get("updated_at") is not None:
        updates["updated_at"] = record["updated_at"]
    name = record.get("name")
    if name is not None and str(name).strip():
        updates["name"] = str(name).strip()
    if "industry_ths" in record and record["industry_ths"] is not None:
        updates["industry_ths"] = record["industry_ths"]
    if "sector_name" in record and record["sector_name"] is not None:
        updates["sector_name"] = record["sector_name"]
    if record.get("listing_date") is not None:
        updates["listing_date"] = record["listing_date"]
    if record.get("status") is not None:
        updates["status"] = record["status"]
    return updates


class FundamentalRepository:
    """基本面数据访问层。

    封装 StockListing / FinancialAbstract / ValuationDaily 三张表的 CRUD 操作。
    """

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        """初始化数据访问层。

        Args:
            db_manager: 数据库管理器（可选，默认使用单例）
        """
        self.db = db_manager or DatabaseManager.get_instance()

    # ========================================================================
    # StockListing — 全A股股票清单
    # ========================================================================

    def upsert_stock_listing(self, record: Dict[str, Any]) -> bool:
        """插入或更新单条股票清单记录。

        Args:
            record: 包含 code, name, market 等字段的字典

        Returns:
            是否成功
        """
        code = record.get("code")
        if not code:
            return False

        updates = _listing_conflict_updates(record)
        if not updates:
            return True

        try:
            with self.db.get_session() as session:
                existing = session.scalar(
                    select(StockListing).where(StockListing.code == code)
                )
                if existing:
                    for field, value in updates.items():
                        setattr(existing, field, value)
                    session.commit()
                    return True

                name = str(record.get("name") or "").strip()
                market = record.get("market")
                if not name or not market:
                    logger.error(
                        "upsert stock listing %s failed: missing name/market for insert",
                        code,
                    )
                    return False

                insert_record: Dict[str, Any] = {
                    "code": code,
                    "name": name,
                    "market": market,
                    "status": record.get("status") or "listed",
                    "updated_at": record.get("updated_at"),
                }
                for field in ("industry_ths", "sector_name", "listing_date"):
                    if field in record and record[field] is not None:
                        insert_record[field] = record[field]
                session.add(StockListing(**insert_record))
                session.commit()
                return True
        except Exception as e:
            logger.error(f"upsert stock listing {code} failed: {e}")
            return False

    def upsert_stock_listings_batch(self, records: List[Dict[str, Any]]) -> int:
        """批量插入或更新股票清单记录。

        Args:
            records: 股票清单字典列表

        Returns:
            成功写入的记录数
        """
        if not records:
            return 0
        count = 0
        try:
            with self.db.get_session() as session:
                for record in records:
                    stmt = (
                        sqlite_insert(StockListing)
                        .values(**record)
                        .on_conflict_do_update(
                            index_elements=['code'],
                            set_=_listing_conflict_updates(record),
                        )
                    )
                    session.execute(stmt)
                session.commit()
                count = len(records)
            logger.info(f"Batch upserted {count} stock listings")
        except Exception as e:
            logger.error(f"Batch upsert stock listings failed: {e}")
        return count

    def get_all_listed_stocks(self, market: Optional[str] = None) -> List[StockListing]:
        """获取所有上市股票。

        Args:
            market: 可选，过滤市场 ("SH"/"SZ"/"BJ")

        Returns:
            StockListing 对象列表
        """
        try:
            with self.db.get_session() as session:
                stmt = select(StockListing).where(StockListing.status == 'listed')
                if market:
                    stmt = stmt.where(StockListing.market == market)
                stmt = stmt.order_by(StockListing.code)
                return list(session.execute(stmt).scalars().all())
        except Exception as e:
            logger.error(f"Get all listed stocks failed: {e}")
            return []

    def get_stock_listing_count(self) -> int:
        """获取上市股票总数。"""
        try:
            with self.db.get_session() as session:
                return session.execute(
                    select(func.count(StockListing.id)).where(
                        StockListing.status == 'listed'
                    )
                ).scalar() or 0
        except Exception as e:
            logger.error(f"Get stock listing count failed: {e}")
            return 0

    def get_stock_listing_by_code(self, code: str) -> Optional[StockListing]:
        """按代码获取单条股票清单记录。"""
        normalized_code = str(code or "").strip().split(".")[0]
        if not normalized_code:
            return None
        try:
            with self.db.get_session() as session:
                return session.execute(
                    select(StockListing).where(StockListing.code == normalized_code)
                ).scalar_one_or_none()
        except Exception as e:
            logger.error(f"Get stock listing for {normalized_code} failed: {e}")
            return None

    def get_stocks_without_industry(self) -> List[str]:
        """获取缺少行业分类的股票代码列表。"""
        try:
            with self.db.get_session() as session:
                rows = session.execute(
                    select(StockListing.code)
                    .where(
                        and_(
                            StockListing.status == 'listed',
                            StockListing.industry_ths.is_(None),
                        )
                    )
                    .order_by(StockListing.code)
                ).scalars().all()
                return list(rows)
        except Exception as e:
            logger.error(f"Get stocks without industry failed: {e}")
            return []

    def get_stocks_without_name(self) -> List[str]:
        """获取名称为空的上市股票代码列表。"""
        try:
            with self.db.get_session() as session:
                rows = session.execute(
                    select(StockListing.code)
                    .where(
                        and_(
                            StockListing.status == "listed",
                            or_(
                                StockListing.name.is_(None),
                                StockListing.name == "",
                            ),
                        )
                    )
                    .order_by(StockListing.code)
                ).scalars().all()
                return list(rows)
        except Exception as e:
            logger.error(f"Get stocks without name failed: {e}")
            return []

    def get_stocks_without_financials(self) -> List[str]:
        """获取没有财务数据的股票代码列表（用于增量同步）。"""
        try:
            with self.db.get_session() as session:
                # 子查询：已有财务数据的 code
                sub = select(FinancialAbstract.code).distinct().subquery()
                rows = session.execute(
                    select(StockListing.code)
                    .where(
                        and_(
                            StockListing.status == 'listed',
                            StockListing.code.not_in(select(sub.c.code)),
                        )
                    )
                    .order_by(StockListing.code)
                ).scalars().all()
                return list(rows)
        except Exception as e:
            logger.error(f"Get stocks without financials failed: {e}")
            return []

    # ========================================================================
    # FinancialAbstract — 季度财务指标摘要
    # ========================================================================

    def upsert_financial_abstract(self, record: Dict[str, Any]) -> bool:
        """插入或更新单条财务摘要记录。

        Args:
            record: 包含 code, report_period 及指标字段的字典

        Returns:
            是否成功
        """
        try:
            with self.db.get_session() as session:
                stmt = (
                    sqlite_insert(FinancialAbstract)
                    .values(**record)
                    .on_conflict_do_update(
                        index_elements=['code', 'report_period'],
                        set_={k: v for k, v in record.items()
                              if k not in ('code', 'report_period', 'id')},
                    )
                )
                session.execute(stmt)
                session.commit()
                return True
        except Exception as e:
            logger.error(
                f"upsert financial abstract {record.get('code')} "
                f"{record.get('report_period')} failed: {e}"
            )
            return False

    def upsert_financials_batch(self, records: List[Dict[str, Any]]) -> int:
        """批量插入或更新财务摘要记录。

        Args:
            records: 财务摘要字典列表

        Returns:
            成功写入的记录数
        """
        if not records:
            return 0
        count = 0
        try:
            with self.db.get_session() as session:
                for record in records:
                    stmt = (
                        sqlite_insert(FinancialAbstract)
                        .values(**record)
                        .on_conflict_do_update(
                            index_elements=['code', 'report_period'],
                            set_={k: v for k, v in record.items()
                                  if k not in ('code', 'report_period', 'id')},
                        )
                    )
                    session.execute(stmt)
                session.commit()
                count = len(records)
        except Exception as e:
            logger.error(f"Batch upsert financials failed: {e}")
        return count

    def get_financial_history(
        self, code: str, start_period: Optional[date] = None
    ) -> List[FinancialAbstract]:
        """获取一只股票的财务指标历史。

        Args:
            code: 股票代码
            start_period: 可选，起始报告期过滤

        Returns:
            FinancialAbstract 列表（按报告期降序）
        """
        try:
            with self.db.get_session() as session:
                stmt = (
                    select(FinancialAbstract)
                    .where(FinancialAbstract.code == code)
                )
                if start_period:
                    stmt = stmt.where(FinancialAbstract.report_period >= start_period)
                stmt = stmt.order_by(desc(FinancialAbstract.report_period))
                return list(session.execute(stmt).scalars().all())
        except Exception as e:
            logger.error(f"Get financial history for {code} failed: {e}")
            return []

    def get_latest_financial(self, code: str) -> Optional[FinancialAbstract]:
        """获取一只股票的最新财务指标。

        Args:
            code: 股票代码

        Returns:
            最新的 FinancialAbstract 或 None
        """
        try:
            with self.db.get_session() as session:
                return session.execute(
                    select(FinancialAbstract)
                    .where(FinancialAbstract.code == code)
                    .order_by(desc(FinancialAbstract.report_period))
                    .limit(1)
                ).scalar_one_or_none()
        except Exception as e:
            logger.error(f"Get latest financial for {code} failed: {e}")
            return None

    def get_latest_report_period(self, code: str) -> Optional[date]:
        """获取一只股票最新的报告期日期（用于增量同步判断）。

        Args:
            code: 股票代码

        Returns:
            最新报告期日期或 None
        """
        try:
            with self.db.get_session() as session:
                return session.execute(
                    select(func.max(FinancialAbstract.report_period))
                    .where(FinancialAbstract.code == code)
                ).scalar()
        except Exception as e:
            logger.error(f"Get latest report period for {code} failed: {e}")
            return None

    def get_financial_coverage_count(self) -> int:
        """获取有财务数据的股票数量。"""
        try:
            with self.db.get_session() as session:
                return session.execute(
                    select(func.count(func.distinct(FinancialAbstract.code)))
                ).scalar() or 0
        except Exception as e:
            logger.error(f"Get financial coverage count failed: {e}")
            return 0

    # ========================================================================
    # ValuationDaily — 每日估值快照
    # ========================================================================

    def upsert_valuation(self, record: Dict[str, Any]) -> bool:
        """插入或更新单条估值快照。

        Args:
            record: 包含 code, trade_date, val_market_cap 的字典

        Returns:
            是否成功
        """
        try:
            with self.db.get_session() as session:
                stmt = (
                    sqlite_insert(ValuationDaily)
                    .values(**record)
                    .on_conflict_do_update(
                        index_elements=['code', 'trade_date'],
                        set_={'val_market_cap': record.get('val_market_cap')},
                    )
                )
                session.execute(stmt)
                session.commit()
                return True
        except Exception as e:
            logger.error(
                f"upsert valuation {record.get('code')} "
                f"{record.get('trade_date')} failed: {e}"
            )
            return False

    def get_valuation_history(
        self, code: str, days: int = 365
    ) -> List[ValuationDaily]:
        """获取一只股票的估值历史。

        Args:
            code: 股票代码
            days: 获取天数

        Returns:
            ValuationDaily 列表（按日期降序）
        """
        try:
            with self.db.get_session() as session:
                return list(
                    session.execute(
                        select(ValuationDaily)
                        .where(ValuationDaily.code == code)
                        .order_by(desc(ValuationDaily.trade_date))
                        .limit(days)
                    ).scalars().all()
                )
        except Exception as e:
            logger.error(f"Get valuation history for {code} failed: {e}")
            return []

    # ========================================================================
    # Aggregated queries for fundamentals browser
    # ========================================================================

    def get_cache_stats(self) -> Dict[str, Any]:
        """Return high-level counts and freshness markers for the local cache."""
        try:
            with self.db.get_session() as session:
                listed_count = session.execute(
                    select(func.count(StockListing.id)).where(
                        StockListing.status == "listed"
                    )
                ).scalar() or 0
                financial_coverage_count = session.execute(
                    select(func.count(func.distinct(FinancialAbstract.code)))
                ).scalar() or 0
                industry_coverage_count = session.execute(
                    select(func.count(StockListing.id)).where(
                        and_(
                            StockListing.status == "listed",
                            StockListing.industry_ths.is_not(None),
                            StockListing.industry_ths != "",
                        )
                    )
                ).scalar() or 0
                last_listing_sync_at = session.execute(
                    select(func.max(StockListing.updated_at))
                ).scalar()
                latest_financial_report_period = session.execute(
                    select(func.max(FinancialAbstract.report_period))
                ).scalar()

                return {
                    "listed_count": int(listed_count),
                    "financial_coverage_count": int(financial_coverage_count),
                    "industry_coverage_count": int(industry_coverage_count),
                    "last_listing_sync_at": last_listing_sync_at,
                    "latest_financial_report_period": latest_financial_report_period,
                }
        except Exception as e:
            logger.error(f"Get cache stats failed: {e}")
            return {
                "listed_count": 0,
                "financial_coverage_count": 0,
                "industry_coverage_count": 0,
                "last_listing_sync_at": None,
                "latest_financial_report_period": None,
            }

    def list_industries_with_counts(self) -> List[Dict[str, Any]]:
        """List THS industries with stock counts, plus an unclassified bucket."""
        try:
            with self.db.get_session() as session:
                rows = session.execute(
                    select(StockListing.industry_ths, func.count(StockListing.id))
                    .where(
                        and_(
                            StockListing.status == "listed",
                            StockListing.industry_ths.is_not(None),
                            StockListing.industry_ths != "",
                        )
                    )
                    .group_by(StockListing.industry_ths)
                    .order_by(desc(func.count(StockListing.id)), StockListing.industry_ths)
                ).all()

                items = [
                    {"name": str(name), "stock_count": int(count)}
                    for name, count in rows
                    if name
                ]

                unclassified_count = session.execute(
                    select(func.count(StockListing.id)).where(
                        and_(
                            StockListing.status == "listed",
                            or_(
                                StockListing.industry_ths.is_(None),
                                StockListing.industry_ths == "",
                            ),
                        )
                    )
                ).scalar() or 0
                if unclassified_count:
                    items.append(
                        {
                            "name": UNCLASSIFIED_INDUSTRY_KEY,
                            "stock_count": int(unclassified_count),
                        }
                    )
                return items
        except Exception as e:
            logger.error(f"List industries failed: {e}")
            return []

    def search_stock_listings(
        self,
        *,
        search: Optional[str] = None,
        market: Optional[str] = None,
        industry: Optional[str] = None,
        industry_exact: bool = False,
        page: int = 1,
        limit: int = 20,
        sort_by: str = "code",
        sort_order: str = "asc",
    ) -> Tuple[int, List[Dict[str, Any]]]:
        """Search stock listings with latest financial snapshot per row."""
        try:
            with self.db.get_session() as session:
                latest_fin_subq = (
                    select(
                        FinancialAbstract.code.label("fin_code"),
                        func.max(FinancialAbstract.report_period).label("max_period"),
                    )
                    .group_by(FinancialAbstract.code)
                    .subquery()
                )

                stmt = (
                    select(StockListing, FinancialAbstract)
                    .outerjoin(
                        latest_fin_subq,
                        StockListing.code == latest_fin_subq.c.fin_code,
                    )
                    .outerjoin(
                        FinancialAbstract,
                        and_(
                            FinancialAbstract.code == latest_fin_subq.c.fin_code,
                            FinancialAbstract.report_period == latest_fin_subq.c.max_period,
                        ),
                    )
                    .where(StockListing.status == "listed")
                )

                conditions = []
                if search:
                    search_pattern = f"%{search.strip()}%"
                    conditions.append(
                        or_(
                            StockListing.code.like(search_pattern),
                            StockListing.name.like(search_pattern),
                        )
                    )
                if market:
                    conditions.append(StockListing.market == market.upper())
                if industry:
                    if industry == UNCLASSIFIED_INDUSTRY_KEY:
                        conditions.append(
                            or_(
                                StockListing.industry_ths.is_(None),
                                StockListing.industry_ths == "",
                            )
                        )
                    elif industry_exact:
                        conditions.append(StockListing.industry_ths == industry)
                    else:
                        conditions.append(
                            StockListing.industry_ths.like(f"%{industry}%")
                        )
                if conditions:
                    stmt = stmt.where(and_(*conditions))

                count_stmt = select(func.count()).select_from(stmt.subquery())
                total = session.execute(count_stmt).scalar() or 0

                sort_column = _SORTABLE_FIELDS.get(sort_by, StockListing.code)
                order_desc = sort_order.lower() == "desc"
                if sort_column is StockListing.code:
                    order_exprs = [desc(sort_column) if order_desc else sort_column.asc()]
                else:
                    order_exprs = [
                        sort_column.is_(None),
                        desc(sort_column) if order_desc else sort_column.asc(),
                    ]

                offset = max(0, (page - 1) * limit)
                rows = session.execute(
                    stmt.order_by(*order_exprs).offset(offset).limit(limit)
                ).all()

                items: List[Dict[str, Any]] = []
                for stock, financial in rows:
                    items.append(
                        {
                            "code": stock.code,
                            "name": stock.name,
                            "market": stock.market,
                            "industry_ths": stock.industry_ths,
                            "sector_name": stock.sector_name,
                            "status": stock.status,
                            "listing_date": stock.listing_date,
                            "has_financial": financial is not None,
                            "latest_report_period": (
                                financial.report_period if financial else None
                            ),
                            "revenue": financial.revenue if financial else None,
                            "revenue_yoy": financial.revenue_yoy if financial else None,
                            "net_margin": financial.net_margin if financial else None,
                            "roe": financial.roe if financial else None,
                        }
                    )
                return int(total), items
        except Exception as e:
            logger.error(f"Search stock listings failed: {e}")
            return 0, []
