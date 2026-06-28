# -*- coding: utf-8 -*-
"""基本面数据 API Schema 定义"""

from typing import Optional, List
from pydantic import BaseModel, ConfigDict, Field


# ========================================================================
# 股票清单
# ========================================================================


class StockListItem(BaseModel):
    """股票清单列表项（概览）"""

    code: str = Field(..., description="股票代码")
    name: str = Field(..., description="股票名称")
    market: str = Field(..., description="市场 (SH/SZ/BJ)")
    industry_ths: Optional[str] = Field(None, description="同花顺行业")
    sector_name: Optional[str] = Field(None, description="板块名称")
    status: str = Field(..., description="上市状态")
    listing_date: Optional[str] = Field(None, description="上市日期")
    has_financial: bool = Field(False, description="是否有本地财务摘要")
    latest_report_period: Optional[str] = Field(None, description="最新财务报告期")
    revenue: Optional[float] = Field(None, description="最新营业总收入（元）")
    revenue_yoy: Optional[float] = Field(None, description="最新营收同比 (%)")
    net_margin: Optional[float] = Field(None, description="最新销售净利率 (%)")
    roe: Optional[float] = Field(None, description="最新净资产收益率 (%)")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": "600519",
                "name": "贵州茅台",
                "market": "SH",
                "industry_ths": "白酒",
                "sector_name": None,
                "status": "listed",
                "listing_date": None,
                "has_financial": True,
                "latest_report_period": "2025-12-31",
                "revenue": 172054000000.0,
                "revenue_yoy": -1.2,
                "net_margin": 50.53,
                "roe": 32.53,
            }
        }
    )


class StockListResponse(BaseModel):
    """股票清单分页响应"""

    total: int = Field(..., description="总记录数")
    page: int = Field(..., description="当前页码")
    limit: int = Field(..., description="每页数量")
    items: List[StockListItem] = Field(default_factory=list, description="股票列表")


class IndustryItem(BaseModel):
    """行业分类统计项"""

    name: str = Field(..., description="行业名称")
    stock_count: int = Field(..., description="该行业股票数量")


class IndustryListResponse(BaseModel):
    """行业分类列表响应"""

    total: int = Field(..., description="行业总数")
    items: List[IndustryItem] = Field(default_factory=list, description="行业列表")


class FundamentalsCacheStatsResponse(BaseModel):
    """本地基本面缓存统计"""

    listed_count: int = Field(..., description="上市股票总数")
    financial_coverage_count: int = Field(..., description="有财务摘要的股票数")
    industry_coverage_count: int = Field(..., description="有行业分类的股票数")
    last_listing_sync_at: Optional[str] = Field(None, description="股票清单最近更新时间")
    latest_financial_report_period: Optional[str] = Field(
        None,
        description="库中最新财务报告期",
    )


# ========================================================================
# 财务指标
# ========================================================================


class FinancialIndicator(BaseModel):
    """单个报告期的财务指标"""

    report_period: str = Field(..., description="报告期 (YYYY-MM-DD)")
    net_profit: Optional[float] = Field(None, description="净利润（元）")
    net_profit_yoy: Optional[float] = Field(None, description="净利润同比增长率 (%)")
    revenue: Optional[float] = Field(None, description="营业总收入（元）")
    revenue_yoy: Optional[float] = Field(None, description="营收同比增长率 (%)")
    eps: Optional[float] = Field(None, description="基本每股收益")
    bvps: Optional[float] = Field(None, description="每股净资产")
    gross_margin: Optional[float] = Field(None, description="销售毛利率 (%)")
    net_margin: Optional[float] = Field(None, description="销售净利率 (%)")
    roe: Optional[float] = Field(None, description="净资产收益率 (%)")
    roe_diluted: Optional[float] = Field(None, description="ROE-摊薄 (%)")
    current_ratio: Optional[float] = Field(None, description="流动比率")
    quick_ratio: Optional[float] = Field(None, description="速动比率")
    debt_ratio: Optional[float] = Field(None, description="资产负债率 (%)")
    capital_reserve_ps: Optional[float] = Field(None, description="每股资本公积金")
    retained_earnings_ps: Optional[float] = Field(None, description="每股未分配利润")
    operating_cf_ps: Optional[float] = Field(None, description="每股经营现金流")
    inventory_turnover: Optional[float] = Field(None, description="存货周转率")
    receivables_turnover_days: Optional[float] = Field(None, description="应收账款周转天数")
    operating_cycle: Optional[float] = Field(None, description="营业周期（天）")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "report_period": "2025-12-31",
                "net_profit": 82320000000.0,
                "net_profit_yoy": -4.53,
                "revenue": 172054000000.0,
                "revenue_yoy": -1.20,
                "eps": 65.66,
                "bvps": 195.36,
                "gross_margin": 91.18,
                "net_margin": 50.53,
                "roe": 32.53,
                "roe_diluted": 33.65,
                "current_ratio": 5.09,
                "quick_ratio": 3.31,
                "debt_ratio": 16.42,
            }
        }
    )


class FinancialHistoryResponse(BaseModel):
    """财务历史响应"""

    code: str = Field(..., description="股票代码")
    name: Optional[str] = Field(None, description="股票名称")
    periods: List[FinancialIndicator] = Field(default_factory=list, description="财务数据列表")


# ========================================================================
# 股票详情
# ========================================================================


class StockDetailResponse(BaseModel):
    """股票详情（含最新财务数据）"""

    code: str = Field(..., description="股票代码")
    name: str = Field(..., description="股票名称")
    market: str = Field(..., description="市场")
    industry_ths: Optional[str] = Field(None, description="行业分类")
    sector_name: Optional[str] = Field(None, description="板块名称")
    status: str = Field(..., description="上市状态")
    listing_date: Optional[str] = Field(None, description="上市日期")
    latest_financial: Optional[FinancialIndicator] = Field(None, description="最新财务指标")
    financial_history: List[FinancialIndicator] = Field(default_factory=list, description="最近 8 期财务数据")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": "600519",
                "name": "贵州茅台",
                "market": "SH",
                "industry_ths": "白酒",
                "sector_name": None,
                "status": "listed",
                "listing_date": None,
                "latest_financial": {
                    "report_period": "2026-03-31",
                    "net_profit": 27243000000.0,
                    "revenue": 54703000000.0,
                    "roe": 10.57,
                    "gross_margin": 89.76,
                },
                "financial_history": [],
            }
        }
    )
