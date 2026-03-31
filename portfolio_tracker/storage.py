"""Persistence minimale de la V2."""

from __future__ import annotations

from datetime import date, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any


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

            CREATE TABLE IF NOT EXISTS app_meta (
                meta_key TEXT PRIMARY KEY,
                meta_value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS assets (
                asset_id TEXT PRIMARY KEY,
                asset_type TEXT NOT NULL,
                name TEXT NOT NULL,
                valuation_engine TEXT NOT NULL,
                isin TEXT,
                metadata_json TEXT NOT NULL,
                imported_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS positions (
                position_id TEXT PRIMARY KEY,
                asset_id TEXT NOT NULL,
                holder_type TEXT NOT NULL,
                wrapper_type TEXT NOT NULL,
                insurer TEXT NOT NULL,
                contract_name TEXT NOT NULL,
                subscription_date TEXT NOT NULL,
                invested_amount REAL,
                units_held REAL,
                purchase_nav REAL,
                purchase_nav_currency TEXT NOT NULL,
                purchase_nav_source TEXT,
                imported_at TEXT NOT NULL,
                FOREIGN KEY (asset_id) REFERENCES assets(asset_id)
            );

            CREATE TABLE IF NOT EXISTS position_lots (
                position_id TEXT NOT NULL,
                lot_index INTEGER NOT NULL,
                lot_date TEXT,
                raw_lot_type TEXT,
                raw_lot_json TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                PRIMARY KEY (position_id, lot_index),
                FOREIGN KEY (position_id) REFERENCES positions(position_id)
            );

            CREATE INDEX IF NOT EXISTS idx_positions_contract_name
            ON positions(contract_name);

            CREATE INDEX IF NOT EXISTS idx_position_lots_position_date
            ON position_lots(position_id, lot_date);

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

            CREATE TABLE IF NOT EXISTS snapshot_positions (
                snapshot_position_id TEXT PRIMARY KEY,
                snapshot_id TEXT NOT NULL,
                contract_id TEXT NOT NULL,
                contract_name TEXT NOT NULL,
                position_id TEXT,
                asset_id TEXT,
                asset_type TEXT,
                asset_name_raw TEXT NOT NULL,
                isin TEXT,
                valuation_date TEXT,
                quantity REAL,
                unit_value REAL,
                official_value REAL NOT NULL,
                official_cost_basis REAL,
                official_profit_sharing_amount REAL,
                official_average_purchase_price REAL,
                status TEXT NOT NULL,
                notes TEXT,
                imported_at TEXT NOT NULL,
                FOREIGN KEY (snapshot_id) REFERENCES annual_snapshots(snapshot_id),
                FOREIGN KEY (contract_id) REFERENCES contracts(contract_id)
            );

            CREATE INDEX IF NOT EXISTS idx_snapshot_positions_snapshot_id
            ON snapshot_positions(snapshot_id);

            CREATE TABLE IF NOT EXISTS snapshot_operations_visible (
                snapshot_operation_id TEXT PRIMARY KEY,
                snapshot_id TEXT NOT NULL,
                contract_id TEXT NOT NULL,
                contract_name TEXT NOT NULL,
                operation_label TEXT NOT NULL,
                operation_type TEXT NOT NULL,
                effective_date TEXT NOT NULL,
                headline_amount REAL,
                fees_info_raw TEXT,
                status TEXT NOT NULL,
                notes TEXT,
                imported_at TEXT NOT NULL,
                FOREIGN KEY (snapshot_id) REFERENCES annual_snapshots(snapshot_id),
                FOREIGN KEY (contract_id) REFERENCES contracts(contract_id)
            );

            CREATE TABLE IF NOT EXISTS snapshot_operation_legs_visible (
                snapshot_operation_leg_id TEXT PRIMARY KEY,
                snapshot_operation_id TEXT NOT NULL,
                snapshot_id TEXT NOT NULL,
                contract_id TEXT NOT NULL,
                contract_name TEXT NOT NULL,
                position_id TEXT,
                asset_id TEXT,
                asset_type TEXT,
                asset_name_raw TEXT NOT NULL,
                effective_date TEXT,
                cash_amount REAL NOT NULL,
                quantity REAL,
                unit_value REAL,
                direction TEXT NOT NULL,
                notes TEXT,
                imported_at TEXT NOT NULL,
                FOREIGN KEY (snapshot_operation_id) REFERENCES snapshot_operations_visible(snapshot_operation_id),
                FOREIGN KEY (snapshot_id) REFERENCES annual_snapshots(snapshot_id),
                FOREIGN KEY (contract_id) REFERENCES contracts(contract_id)
            );

            CREATE INDEX IF NOT EXISTS idx_snapshot_operations_visible_snapshot_id
            ON snapshot_operations_visible(snapshot_id);

            CREATE INDEX IF NOT EXISTS idx_snapshot_operation_legs_visible_operation_id
            ON snapshot_operation_legs_visible(snapshot_operation_id);

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

            CREATE TABLE IF NOT EXISTS manual_movements (
                manual_movement_id TEXT PRIMARY KEY,
                contract_id TEXT NOT NULL,
                contract_name TEXT NOT NULL,
                position_id TEXT,
                asset_id TEXT NOT NULL,
                asset_name TEXT NOT NULL,
                bucket TEXT NOT NULL,
                effective_date TEXT NOT NULL,
                raw_lot_type TEXT NOT NULL,
                movement_kind TEXT NOT NULL,
                cash_amount REAL NOT NULL,
                units_delta REAL,
                unit_price REAL,
                external_flag INTEGER,
                linked_document_id TEXT,
                reason TEXT NOT NULL,
                notes TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (contract_id) REFERENCES contracts(contract_id),
                FOREIGN KEY (linked_document_id) REFERENCES documents(document_id)
            );

            CREATE TABLE IF NOT EXISTS document_movements (
                document_movement_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                contract_id TEXT NOT NULL,
                contract_name TEXT NOT NULL,
                position_id TEXT,
                asset_id TEXT NOT NULL,
                asset_name TEXT NOT NULL,
                bucket TEXT NOT NULL,
                effective_date TEXT NOT NULL,
                raw_lot_type TEXT NOT NULL,
                movement_kind TEXT NOT NULL,
                cash_amount REAL NOT NULL,
                units_delta REAL,
                unit_price REAL,
                external_flag INTEGER,
                notes TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (contract_id) REFERENCES contracts(contract_id),
                FOREIGN KEY (document_id) REFERENCES documents(document_id)
            );

            CREATE INDEX IF NOT EXISTS idx_document_movements_document_id
            ON document_movements(document_id);

            CREATE TABLE IF NOT EXISTS market_series (
                kind TEXT NOT NULL,
                identifier TEXT NOT NULL,
                source TEXT,
                currency TEXT,
                source_url TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (kind, identifier)
            );

            CREATE TABLE IF NOT EXISTS market_series_points (
                kind TEXT NOT NULL,
                identifier TEXT NOT NULL,
                point_date TEXT NOT NULL,
                value REAL NOT NULL,
                currency TEXT,
                point_source TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (kind, identifier, point_date),
                FOREIGN KEY (kind, identifier) REFERENCES market_series(kind, identifier)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_market_series_points_lookup
            ON market_series_points(kind, identifier, point_date);

            CREATE TABLE IF NOT EXISTS structured_events (
                asset_id TEXT NOT NULL,
                is_expected INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                event_date TEXT NOT NULL,
                amount REAL,
                description TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                imported_at TEXT NOT NULL,
                PRIMARY KEY (asset_id, is_expected, event_type, event_date)
            );

            CREATE INDEX IF NOT EXISTS idx_structured_events_asset_id
            ON structured_events(asset_id);
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


def _series_table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'market_series_points'"
    ).fetchone()
    return row is not None


def _as_iso_date(value: date | str) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _json_dumps(payload: dict[str, Any] | None) -> str:
    return json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)


def upsert_market_series_metadata(
    db_path: Path,
    *,
    kind: str,
    identifier: str,
    source: str | None = None,
    currency: str | None = None,
    source_url: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    init_db(db_path)
    now = datetime.now().isoformat(timespec="seconds")
    with connect(db_path) as conn:
        existing = conn.execute(
            """
            SELECT source, currency, source_url, metadata_json
            FROM market_series
            WHERE kind = ? AND identifier = ?
            """,
            (kind, identifier),
        ).fetchone()
        merged_metadata = dict(json.loads(str(existing["metadata_json"] or "{}"))) if existing else {}
        if metadata:
            merged_metadata.update(metadata)
        conn.execute(
            """
            INSERT INTO market_series (
                kind, identifier, source, currency, source_url, metadata_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(kind, identifier) DO UPDATE SET
                source = excluded.source,
                currency = excluded.currency,
                source_url = excluded.source_url,
                metadata_json = excluded.metadata_json,
                updated_at = excluded.updated_at
            """,
            (
                kind,
                identifier,
                source if source is not None else (str(existing["source"]) if existing and existing["source"] is not None else None),
                currency if currency is not None else (str(existing["currency"]) if existing and existing["currency"] is not None else None),
                source_url if source_url is not None else (str(existing["source_url"]) if existing and existing["source_url"] is not None else None),
                _json_dumps(merged_metadata),
                now,
            ),
        )


def upsert_market_series_points(
    db_path: Path,
    *,
    kind: str,
    identifier: str,
    points: list[dict[str, Any]],
    source: str | None = None,
    currency: str | None = None,
    source_url: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> int:
    init_db(db_path)
    normalized_points = []
    for point in points:
        raw_date = point.get("point_date", point.get("date"))
        raw_value = point.get("value")
        if raw_date in (None, "") or raw_value is None:
            continue
        normalized_points.append(
            {
                "point_date": _as_iso_date(raw_date),
                "value": float(raw_value),
                "currency": point.get("currency"),
                "point_source": point.get("source"),
            }
        )

    upsert_market_series_metadata(
        db_path,
        kind=kind,
        identifier=identifier,
        source=source,
        currency=currency,
        source_url=source_url,
        metadata=metadata,
    )
    if not normalized_points:
        return 0

    now = datetime.now().isoformat(timespec="seconds")
    with connect(db_path) as conn:
        existing_rows = {
            str(row["point_date"]): row
            for row in conn.execute(
                """
                SELECT point_date, value, currency, point_source
                FROM market_series_points
                WHERE kind = ? AND identifier = ?
                """,
                (kind, identifier),
            ).fetchall()
        }
        changed = 0
        for point in normalized_points:
            previous = existing_rows.get(point["point_date"])
            previous_tuple = None
            if previous is not None:
                previous_tuple = (
                    float(previous["value"]),
                    str(previous["currency"]) if previous["currency"] is not None else None,
                    str(previous["point_source"]) if previous["point_source"] is not None else None,
                )
            current_tuple = (
                float(point["value"]),
                str(point["currency"]) if point["currency"] is not None else None,
                str(point["point_source"]) if point["point_source"] is not None else None,
            )
            if previous_tuple == current_tuple:
                continue
            conn.execute(
                """
                INSERT INTO market_series_points (
                    kind, identifier, point_date, value, currency, point_source, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(kind, identifier, point_date) DO UPDATE SET
                    value = excluded.value,
                    currency = excluded.currency,
                    point_source = excluded.point_source,
                    updated_at = excluded.updated_at
                """,
                (
                    kind,
                    identifier,
                    point["point_date"],
                    point["value"],
                    point["currency"],
                    point["point_source"],
                    now,
                ),
            )
            changed += 1
        return changed


def get_market_series_points(
    db_path: Path,
    *,
    kind: str,
    identifier: str,
    date_from: str | date | None = None,
    date_to: str | date | None = None,
) -> list[dict[str, Any]]:
    db_path = Path(db_path)
    if not db_path.exists():
        return []
    with connect(db_path) as conn:
        if not _series_table_exists(conn):
            return []
        query = [
            """
            SELECT
                p.point_date,
                p.value,
                COALESCE(p.currency, s.currency) AS currency,
                COALESCE(p.point_source, s.source) AS point_source
            FROM market_series_points p
            LEFT JOIN market_series s
              ON s.kind = p.kind AND s.identifier = p.identifier
            WHERE p.kind = ? AND p.identifier = ?
            """
        ]
        params: list[Any] = [kind, identifier]
        if date_from is not None:
            query.append("AND p.point_date >= ?")
            params.append(_as_iso_date(date_from))
        if date_to is not None:
            query.append("AND p.point_date <= ?")
            params.append(_as_iso_date(date_to))
        query.append("ORDER BY p.point_date")
        rows = conn.execute("\n".join(query), tuple(params)).fetchall()
        return [
            {
                "date": str(row["point_date"]),
                "value": float(row["value"]),
                "currency": str(row["currency"]) if row["currency"] is not None else None,
                "source": str(row["point_source"]) if row["point_source"] is not None else None,
            }
            for row in rows
        ]


def get_market_series_latest(
    db_path: Path,
    *,
    kind: str,
    identifier: str,
    target_date: str | date | None = None,
) -> dict[str, Any] | None:
    points = get_market_series_points(
        db_path,
        kind=kind,
        identifier=identifier,
        date_to=target_date,
    )
    if not points:
        return None
    latest = points[-1]
    return {
        "date": date.fromisoformat(str(latest["date"])),
        "value": float(latest["value"]),
        "currency": latest.get("currency"),
        "source": latest.get("source"),
    }


def get_market_series_metadata(
    db_path: Path,
    *,
    kind: str,
    identifier: str,
) -> dict[str, Any] | None:
    db_path = Path(db_path)
    if not db_path.exists():
        return None
    with connect(db_path) as conn:
        if not _series_table_exists(conn):
            return None
        row = conn.execute(
            """
            SELECT source, currency, source_url, metadata_json, updated_at
            FROM market_series
            WHERE kind = ? AND identifier = ?
            """,
            (kind, identifier),
        ).fetchone()
        if row is None:
            return None
        metadata = dict(json.loads(str(row["metadata_json"] or "{}")) or {})
        return {
            "source": str(row["source"]) if row["source"] is not None else None,
            "currency": str(row["currency"]) if row["currency"] is not None else None,
            "source_url": str(row["source_url"]) if row["source_url"] is not None else None,
            "updated_at": str(row["updated_at"]),
            "metadata": metadata,
        }


def get_market_series_summary(
    db_path: Path,
    *,
    kind: str,
    identifier: str,
) -> dict[str, Any] | None:
    db_path = Path(db_path)
    if not db_path.exists():
        return None
    with connect(db_path) as conn:
        if not _series_table_exists(conn):
            return None
        aggregate = conn.execute(
            """
            SELECT
                COUNT(*) AS points_count,
                MIN(point_date) AS earliest_date,
                MAX(point_date) AS latest_date
            FROM market_series_points
            WHERE kind = ? AND identifier = ?
            """,
            (kind, identifier),
        ).fetchone()
        metadata = get_market_series_metadata(db_path, kind=kind, identifier=identifier) or {}
        latest_row = conn.execute(
            """
            SELECT
                p.value,
                COALESCE(p.point_source, s.source) AS point_source
            FROM market_series_points p
            LEFT JOIN market_series s
              ON s.kind = p.kind AND s.identifier = p.identifier
            WHERE p.kind = ? AND p.identifier = ?
            ORDER BY p.point_date DESC
            LIMIT 1
            """,
            (kind, identifier),
        ).fetchone()
        if aggregate is None or int(aggregate["points_count"] or 0) == 0:
            if metadata:
                return {
                    "points_count": 0,
                    "earliest_date": None,
                    "latest_date": None,
                    "latest_value": None,
                    "source": metadata.get("source"),
                    "currency": metadata.get("currency"),
                    "source_url": metadata.get("source_url"),
                    "metadata": metadata.get("metadata") or {},
                }
            return None
        return {
            "points_count": int(aggregate["points_count"] or 0),
            "earliest_date": str(aggregate["earliest_date"]) if aggregate["earliest_date"] is not None else None,
            "latest_date": str(aggregate["latest_date"]) if aggregate["latest_date"] is not None else None,
            "latest_value": float(latest_row["value"]) if latest_row is not None else None,
            "source": metadata.get("source") if metadata else None,
            "currency": metadata.get("currency") if metadata else None,
            "source_url": metadata.get("source_url") if metadata else None,
            "metadata": metadata.get("metadata") if metadata else {},
        }


def upsert_structured_events(
    db_path: Path,
    *,
    asset_id: str,
    events: list[dict[str, Any]],
    is_expected: bool,
) -> int:
    init_db(db_path)
    now = datetime.now().isoformat(timespec="seconds")
    is_exp_int = 1 if is_expected else 0
    changed = 0
    with connect(db_path) as conn:
        for event in events:
            event_type = str(event.get("type") or event.get("event_type") or "")
            event_date = str(event.get("date") or event.get("event_date") or "")
            if not event_type or not event_date:
                continue
            conn.execute(
                """
                INSERT INTO structured_events
                    (asset_id, is_expected, event_type, event_date, amount, description, metadata_json, imported_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(asset_id, is_expected, event_type, event_date) DO UPDATE SET
                    amount = excluded.amount,
                    description = excluded.description,
                    metadata_json = excluded.metadata_json,
                    imported_at = excluded.imported_at
                """,
                (
                    asset_id,
                    is_exp_int,
                    event_type,
                    event_date,
                    event.get("amount"),
                    str(event.get("description") or ""),
                    json.dumps(event.get("metadata") or {}),
                    now,
                ),
            )
            changed += 1
    return changed


def get_structured_events(
    db_path: Path,
    *,
    asset_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Returns (realized_events, expected_events) for the given asset."""
    db_path = Path(db_path)
    if not db_path.exists():
        return [], []
    init_db(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT is_expected, event_type, event_date, amount, description, metadata_json
            FROM structured_events
            WHERE asset_id = ?
            ORDER BY event_date
            """,
            (asset_id,),
        ).fetchall()
    realized: list[dict[str, Any]] = []
    expected: list[dict[str, Any]] = []
    for row in rows:
        event: dict[str, Any] = {
            "type": str(row["event_type"]),
            "date": str(row["event_date"]),
            "amount": float(row["amount"]) if row["amount"] is not None else None,
            "description": str(row["description"] or ""),
            "metadata": json.loads(str(row["metadata_json"] or "{}")),
        }
        if row["is_expected"]:
            expected.append(event)
        else:
            realized.append(event)
    return realized, expected
