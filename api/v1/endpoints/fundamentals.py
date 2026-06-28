# -*- coding: utf-8 -*-
"""
===================================
基本面数据 API 端点
===================================

职责：
1. 提供股票基本面数据查询接口
2. 支持分页、搜索、筛选
3. 提供单股详情和财务历史
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from api.deps import get_database_manager
from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.fundamentals import (
    FinancialHistoryResponse,
    FinancialIndicator,
    FundamentalsCacheStatsResponse,
    IndustryItem,
    IndustryListResponse,
    StockDetailResponse,
    StockListItem,
    StockListResponse,
)
from src.repositories.fundamental_repo import FundamentalRepository
from src.storage import DatabaseManager, FinancialAbstract, StockListing

logger = logging.getLogger(__name__)
router = APIRouter()


def _row_to_stock_item(row: dict) -> StockListItem:
    listing_date = row.get("listing_date")
    report_period = row.get("latest_report_period")
    return StockListItem(
        code=row["code"],
        name=row["name"],
        market=row["market"],
        industry_ths=row.get("industry_ths"),
        sector_name=row.get("sector_name"),
        status=row["status"],
        listing_date=(
            listing_date.isoformat()
            if listing_date is not None and hasattr(listing_date, "isoformat")
            else listing_date
        ),
        has_financial=bool(row.get("has_financial")),
        latest_report_period=(
            report_period.isoformat()
            if report_period is not None and hasattr(report_period, "isoformat")
            else report_period
        ),
        revenue=row.get("revenue"),
        revenue_yoy=row.get("revenue_yoy"),
        net_margin=row.get("net_margin"),
        roe=row.get("roe"),
    )


def _orm_to_financial_indicator(f: FinancialAbstract) -> FinancialIndicator:
    """ORM 对象转为财务指标 Pydantic 模型。"""
    return FinancialIndicator(
        report_period=f.report_period.isoformat() if f.report_period else None,
        net_profit=f.net_profit,
        net_profit_yoy=f.net_profit_yoy,
        revenue=f.revenue,
        revenue_yoy=f.revenue_yoy,
        eps=f.eps,
        bvps=f.bvps,
        gross_margin=f.gross_margin,
        net_margin=f.net_margin,
        roe=f.roe,
        roe_diluted=f.roe_diluted,
        current_ratio=f.current_ratio,
        quick_ratio=f.quick_ratio,
        debt_ratio=f.debt_ratio,
        capital_reserve_ps=f.capital_reserve_ps,
        retained_earnings_ps=f.retained_earnings_ps,
        operating_cf_ps=f.operating_cf_ps,
        inventory_turnover=f.inventory_turnover,
        receivables_turnover_days=f.receivables_turnover_days,
        operating_cycle=f.operating_cycle,
    )


# ========================================================================
# GET /stats — 本地缓存统计
# ========================================================================


@router.get(
    "/stats",
    response_model=FundamentalsCacheStatsResponse,
    responses={
        200: {"description": "缓存统计"},
        500: {"description": "服务器错误", "model": ErrorResponse},
    },
    summary="本地基本面缓存统计",
    description="返回股票清单、财务覆盖、行业覆盖与最近同步时间。",
)
def get_cache_stats(
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> FundamentalsCacheStatsResponse:
    try:
        repo = FundamentalRepository(db_manager)
        stats = repo.get_cache_stats()
        last_sync = stats.get("last_listing_sync_at")
        latest_period = stats.get("latest_financial_report_period")
        return FundamentalsCacheStatsResponse(
            listed_count=int(stats.get("listed_count") or 0),
            financial_coverage_count=int(stats.get("financial_coverage_count") or 0),
            industry_coverage_count=int(stats.get("industry_coverage_count") or 0),
            last_listing_sync_at=(
                last_sync.isoformat(sep=" ", timespec="seconds")
                if last_sync is not None and hasattr(last_sync, "isoformat")
                else None
            ),
            latest_financial_report_period=(
                latest_period.isoformat()
                if latest_period is not None and hasattr(latest_period, "isoformat")
                else latest_period
            ),
        )
    except Exception as e:
        logger.error(f"查询缓存统计失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": str(e)},
        )


# ========================================================================
# GET /industries — 行业分类列表
# ========================================================================


@router.get(
    "/industries",
    response_model=IndustryListResponse,
    responses={
        200: {"description": "行业列表"},
        500: {"description": "服务器错误", "model": ErrorResponse},
    },
    summary="行业分类列表",
    description="返回本地库中的 THS 行业分类及股票数量。",
)
def list_industries(
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> IndustryListResponse:
    try:
        repo = FundamentalRepository(db_manager)
        rows = repo.list_industries_with_counts()
        items = [
            IndustryItem(name=row["name"], stock_count=int(row["stock_count"]))
            for row in rows
        ]
        return IndustryListResponse(total=len(items), items=items)
    except Exception as e:
        logger.error(f"查询行业列表失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": str(e)},
        )


# ========================================================================
# GET /stocks — 分页查询股票清单（含搜索）
# ========================================================================


@router.get(
    "/stocks",
    response_model=StockListResponse,
    responses={
        200: {"description": "股票列表"},
        500: {"description": "服务器错误", "model": ErrorResponse},
    },
    summary="股票基本面清单",
    description="分页查询全 A 股股票基本面清单，支持按代码、名称、行业、市场筛选。",
)
def list_stocks(
    search: Optional[str] = Query(None, description="搜索关键词（代码或名称，支持模糊匹配）"),
    market: Optional[str] = Query(None, description="市场筛选 (SH/SZ/BJ)"),
    industry: Optional[str] = Query(None, description="行业筛选"),
    industry_exact: bool = Query(False, description="行业是否精确匹配"),
    sort_by: str = Query(
        "code",
        description="排序字段: code, roe, revenue, revenue_yoy, net_margin",
    ),
    sort_order: str = Query("asc", description="排序方向: asc 或 desc"),
    page: int = Query(1, ge=1, description="页码（从 1 开始）"),
    limit: int = Query(20, ge=1, le=100, description="每页数量（最大 100）"),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> StockListResponse:
    try:
        repo = FundamentalRepository(db_manager)
        total, rows = repo.search_stock_listings(
            search=search,
            market=market,
            industry=industry,
            industry_exact=industry_exact,
            page=page,
            limit=limit,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        items = [_row_to_stock_item(row) for row in rows]
        return StockListResponse(total=total, page=page, limit=limit, items=items)

    except Exception as e:
        logger.error(f"查询股票清单失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": str(e)},
        )


# ========================================================================
# GET /stocks/{code} — 股票详情 + 最新财务
# ========================================================================


@router.get(
    "/stocks/{code}",
    response_model=StockDetailResponse,
    responses={
        200: {"description": "股票详情"},
        404: {"description": "股票不存在", "model": ErrorResponse},
        500: {"description": "服务器错误", "model": ErrorResponse},
    },
    summary="股票基本面详情",
    description="获取单只股票的基本面详情，包含最新财务指标和最近 8 期财务历史。",
)
def get_stock_detail(
    code: str,
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> StockDetailResponse:
    try:
        repo = FundamentalRepository(db_manager)

        with db_manager.get_session() as session:
            stock = session.execute(
                select(StockListing).where(StockListing.code == code)
            ).scalar_one_or_none()

            if stock is None:
                raise HTTPException(
                    status_code=404,
                    detail={"error": "not_found", "message": f"股票 {code} 不存在"},
                )

            latest = repo.get_latest_financial(code)
            latest_indicator = _orm_to_financial_indicator(latest) if latest else None

            history = repo.get_financial_history(code)
            history_indicators = [_orm_to_financial_indicator(f) for f in history[:8]]

            return StockDetailResponse(
                code=stock.code,
                name=stock.name,
                market=stock.market,
                industry_ths=stock.industry_ths,
                sector_name=stock.sector_name,
                status=stock.status,
                listing_date=stock.listing_date.isoformat() if stock.listing_date else None,
                latest_financial=latest_indicator,
                financial_history=history_indicators,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"查询股票详情失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": str(e)},
        )


# ========================================================================
# GET /stocks/{code}/financials — 完整财务历史
# ========================================================================


@router.get(
    "/stocks/{code}/financials",
    response_model=FinancialHistoryResponse,
    responses={
        200: {"description": "财务历史"},
        404: {"description": "股票不存在", "model": ErrorResponse},
        500: {"description": "服务器错误", "model": ErrorResponse},
    },
    summary="股票财务历史",
    description="获取单只股票的完整财务指标历史。",
)
def get_financial_history(
    code: str,
    limit: int = Query(20, ge=1, le=100, description="返回的最大记录数"),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> FinancialHistoryResponse:
    try:
        repo = FundamentalRepository(db_manager)

        with db_manager.get_session() as session:
            stock = session.execute(
                select(StockListing).where(StockListing.code == code)
            ).scalar_one_or_none()

            if stock is None:
                raise HTTPException(
                    status_code=404,
                    detail={"error": "not_found", "message": f"股票 {code} 不存在"},
                )

            history = repo.get_financial_history(code)
            indicators = [_orm_to_financial_indicator(f) for f in history[:limit]]

            return FinancialHistoryResponse(
                code=stock.code,
                name=stock.name,
                periods=indicators,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"查询财务历史失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": str(e)},
        )
