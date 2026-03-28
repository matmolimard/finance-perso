"""Persistence minimale de la V2."""

from __future__ import annotations

from pathlib import Path
import sqlite3


def default_db_path(data_dir: Path) -> Path:
    return Path(data_dir) / ".portfolio_tracker_v2.sqlite"


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path) -> None:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS contracts (
                contract_id TEXT PRIMARY KEY,
                contract_name TEXT NOT NULL UNIQUE,
                insurer TEXT NOT NULL,
                holder_type TEXT NOT NULL,
                fiscal_applicability TEXT NOT NULL,
                status TEXT NOT NULL,
                external_contributions_total REAL NOT NULL DEFAULT 0,
                external_withdrawals_total REAL NOT NULL DEFAULT 0,
                notes TEXT
            );

            CREATE TABLE IF NOT EXISTS contract_external_flows (
                contract_id TEXT NOT NULL,
                flow_year INTEGER NOT NULL,
                contributions_total REAL NOT NULL DEFAULT 0,
                withdrawals_total REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (contract_id, flow_year),
                FOREIGN KEY (contract_id) REFERENCES contracts(contract_id)
            );

            CREATE TABLE IF NOT EXISTS documents (
                document_id TEXT PRIMARY KEY,
                document_type TEXT NOT NULL,
                insurer TEXT NOT NULL,
                contract_name TEXT,
                asset_id TEXT,
                document_date TEXT,
                coverage_year INTEGER,
                status TEXT NOT NULL,
                filepath TEXT NOT NULL,
                original_filename TEXT,
                sha256 TEXT,
                notes TEXT,
                imported_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS annual_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                contract_id TEXT NOT NULL,
                contract_name TEXT NOT NULL,
                reference_date TEXT NOT NULL,
                statement_date TEXT,
                source_document_id TEXT,
                status TEXT NOT NULL,
                official_total_value REAL NOT NULL,
                official_uc_value REAL,
                official_fonds_euro_value REAL,
                official_euro_interest_net REAL,
                official_notes TEXT,
                imported_at TEXT NOT NULL,
                FOREIGN KEY (contract_id) REFERENCES contracts(contract_id),
                FOREIGN KEY (source_document_id) REFERENCES documents(document_id)
            );

            CREATE TABLE IF NOT EXISTS contract_ledger_entries (
                entry_id TEXT PRIMARY KEY,
                contract_id TEXT NOT NULL,
                contract_name TEXT NOT NULL,
                position_id TEXT,
                asset_id TEXT,
                asset_name TEXT,
                bucket TEXT NOT NULL,
                entry_date TEXT NOT NULL,
                fiscal_year INTEGER NOT NULL,
                direction TEXT NOT NULL,
                amount REAL NOT NULL,
                units_delta REAL,
                movement_kind TEXT NOT NULL,
                entry_kind TEXT NOT NULL DEFAULT 'other',
                raw_lot_type TEXT,
                external_flag INTEGER,
                source_movement_id TEXT,
                raw_lot_json TEXT,
                imported_at TEXT NOT NULL,
                FOREIGN KEY (contract_id) REFERENCES contracts(contract_id)
            );

            CREATE TABLE IF NOT EXISTS structured_product_rules (
                asset_id TEXT PRIMARY KEY,
                display_name_override TEXT,
                isin_override TEXT,
                rule_source_mode TEXT,
                coupon_payment_mode TEXT,
                coupon_frequency TEXT,
                coupon_rule_summary TEXT,
                autocall_rule_summary TEXT,
                capital_rule_summary TEXT,
                brochure_document_id TEXT,
                notes TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (brochure_document_id) REFERENCES documents(document_id)
            );

            CREATE TABLE IF NOT EXISTS structured_event_validations (
                asset_id TEXT NOT NULL,
                event_key TEXT NOT NULL,
                event_type TEXT NOT NULL,
                event_date TEXT,
                validation_status TEXT NOT NULL,
                notes TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (asset_id, event_key)
            );

            CREATE TABLE IF NOT EXISTS document_validations (
                document_id TEXT PRIMARY KEY,
                validation_status TEXT NOT NULL,
                notes TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (document_id) REFERENCES documents(document_id)
            );

            CREATE TABLE IF NOT EXISTS fonds_euro_pilotage (
                contract_id TEXT PRIMARY KEY,
                pilotage_year INTEGER NOT NULL,
                annual_rate REAL NOT NULL,
                reference_date TEXT NOT NULL,
                notes TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (contract_id) REFERENCES contracts(contract_id)
            );
            """
        )
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(contract_ledger_entries)").fetchall()
        }
        if "entry_kind" not in columns:
            conn.execute(
                "ALTER TABLE contract_ledger_entries ADD COLUMN entry_kind TEXT NOT NULL DEFAULT 'other'"
            )
