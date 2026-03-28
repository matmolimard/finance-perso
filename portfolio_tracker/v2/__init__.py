"""Surface applicative V2 du projet."""

from .bootstrap import bootstrap_v2_data
from .dashboard import build_v2_dashboard_data
from .details import build_v2_contract_detail, build_v2_support_detail
from .documents import build_v2_document_detail
from .ged import build_v2_ged_data
from .market_actions import backfill_v2_market_history, update_v2_uc_navs, update_v2_underlyings
from .market import build_v2_market_data, load_market_series
from .reporting import build_annual_contract_report, build_structured_exercises
from .storage import default_db_path, init_db

__all__ = [
    "bootstrap_v2_data",
    "build_v2_dashboard_data",
    "build_v2_contract_detail",
    "build_v2_support_detail",
    "build_v2_document_detail",
    "build_v2_ged_data",
    "build_v2_market_data",
    "update_v2_uc_navs",
    "update_v2_underlyings",
    "backfill_v2_market_history",
    "load_market_series",
    "build_annual_contract_report",
    "build_structured_exercises",
    "default_db_path",
    "init_db",
]
