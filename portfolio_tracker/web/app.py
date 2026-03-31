"""App web locale V2."""

from __future__ import annotations

from datetime import datetime
from email.parser import BytesParser
from email.policy import default as default_email_policy
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import mimetypes
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from ..bootstrap import bootstrap_v2_data
from ..dashboard import build_v2_dashboard_data
from ..details import build_v2_contract_detail, build_v2_support_detail
from ..documents import build_v2_document_detail
from ..document_ingest import ingest_uploaded_document
from ..ged import build_v2_ged_data
from ..market_actions import backfill_v2_market_history, update_v2_uc_navs, update_v2_underlyings
from ..market import build_v2_market_data, load_market_series
from ..manual import (
    save_document_validation,
    save_fonds_euro_pilotage,
    save_snapshot_validation,
    save_structured_event_validation,
    save_structured_product_rule,
)
from ..manual_market import save_manual_market_point, save_market_source_url
from ..document_extractors import extract_structured_brochure_suggestions
from ..arbitration import (
    apply_arbitration_proposal,
    build_arbitration_proposal_for_document,
    save_arbitration_mappings,
)
from ..storage import connect as connect_v2_db
from ..storage import default_db_path as default_v2_db_path


STATIC_DIR = Path(__file__).resolve().parent / "static"


def _parse_bool(value: Optional[str]) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _optional_float(value: Any) -> Optional[float]:
    if value in (None, "", []):
        return None
    return float(value)


def _optional_int(value: Any) -> Optional[int]:
    if value in (None, "", []):
        return None
    return int(value)


def _split_lines_or_csv(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    parts = []
    for line in str(raw).splitlines():
        for item in line.split(","):
            item = item.strip()
            if item:
                parts.append(item)
    return parts


def _parse_multipart_form_data(content_type: str, body: bytes) -> dict[str, Any]:
    envelope = (
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + body
    )
    message = BytesParser(policy=default_email_policy).parsebytes(envelope)
    form: dict[str, Any] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        filename = part.get_filename()
        content = part.get_payload(decode=True) or b""
        if filename is not None:
            form[name] = {
                "filename": filename,
                "content_type": part.get_content_type(),
                "content": content,
            }
            continue
        charset = part.get_content_charset() or "utf-8"
        form[name] = content.decode(charset)
    return form


def _market_action_status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "ok": sum(1 for row in rows if str(row.get("status") or "") == "ok"),
        "skipped": sum(1 for row in rows if str(row.get("status") or "") == "skipped"),
        "errors": sum(1 for row in rows if str(row.get("status") or "") == "error"),
    }


def _market_action_has_useful_success(result: dict[str, Any]) -> bool:
    if result.get("ok", False):
        return True
    for key in ("results", "uc_results"):
        rows = result.get(key)
        if isinstance(rows, list) and any(str(row.get("status") or "") == "ok" for row in rows if isinstance(row, dict)):
            return True
    nested = result.get("underlyings")
    nested_rows = nested.get("results") if isinstance(nested, dict) else None
    if isinstance(nested_rows, list) and any(str(row.get("status") or "") == "ok" for row in nested_rows if isinstance(row, dict)):
        return True
    return False


def _market_action_http_status(result: dict[str, Any]) -> int:
    if result.get("error"):
        return 400
    return 200 if _market_action_has_useful_success(result) else 400


def _format_market_action_output(command: str, result: dict[str, Any]) -> str:
    if result.get("error"):
        return str(result.get("error"))

    if command == "update-uc-navs":
        summary = result.get("summary") or {}
        lines = [
            f"Mise à jour UC terminée: {int(summary.get('ok') or 0)} ok, {int(summary.get('skipped') or 0)} sans mise à jour, {int(summary.get('errors') or 0)} erreur.",
        ]
        changed_positions = int(result.get("positions_changed") or 0)
        if changed_positions:
            lines.append(f"Positions ajustées: {changed_positions}.")
        errors = [row for row in (result.get("results") or []) if isinstance(row, dict) and row.get("status") == "error"]
        if errors:
            lines.append("Avertissements:")
            lines.extend(f"- {row.get('asset_id')}: {row.get('message')}" for row in errors[:10])
        return "\n".join(lines)

    if command == "update-underlyings":
        summary = result.get("summary") or {}
        counts = _market_action_status_counts([row for row in (result.get("results") or []) if isinstance(row, dict)])
        lines = [
            f"Mise à jour sous-jacents terminée: {counts['ok']} ok, {counts['skipped']} ignoré, {counts['errors']} erreur.",
            f"Points ajoutés/modifiés: {int(summary.get('changed') or 0)}.",
        ]
        errors = [row for row in (result.get("results") or []) if isinstance(row, dict) and row.get("status") == "error"]
        if errors:
            lines.append("Avertissements:")
            lines.extend(f"- {row.get('underlying_id')}: {row.get('message')}" for row in errors[:10])
        return "\n".join(lines)

    if command == "backfill-market-history":
        uc_counts = _market_action_status_counts([row for row in (result.get("uc_results") or []) if isinstance(row, dict)])
        underlyings_payload = result.get("underlyings") if isinstance(result.get("underlyings"), dict) else {}
        underlying_rows = [row for row in (underlyings_payload.get("results") or []) if isinstance(row, dict)]
        underlying_counts = _market_action_status_counts(underlying_rows)
        period = result.get("period") or {}
        mode = "complet" if period.get("mode") == "full" else f"{period.get('years')} an(s)"
        lines = [
            f"Backfill marché {mode} terminé.",
            f"UC: {uc_counts['ok']} ok, {uc_counts['skipped']} sans historique, {uc_counts['errors']} erreur.",
            f"Sous-jacents: {underlying_counts['ok']} ok, {underlying_counts['skipped']} ignoré, {underlying_counts['errors']} erreur.",
        ]
        warning_rows = [row for row in underlying_rows if row.get("status") == "error"]
        if warning_rows:
            lines.append("Avertissements:")
            lines.extend(f"- {row.get('underlying_id')}: {row.get('message')}" for row in warning_rows[:10])
        return "\n".join(lines)

    return ""


def run_action(data_dir: Path, command: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    params = params or {}
    if command == "update-uc-navs":
        result = update_v2_uc_navs(
            data_dir,
            target_date=params.get("target_date") or None,
            set_values=_split_lines_or_csv(params.get("set_values")),
            headless=_parse_bool(params.get("headless")),
        )
    elif command == "update-underlyings":
        result = update_v2_underlyings(
            data_dir,
            headless=_parse_bool(params.get("headless")),
            years=_optional_int(params.get("years")),
        )
    elif command == "backfill-market-history":
        result = backfill_v2_market_history(
            data_dir,
            years=_optional_int(params.get("years")),
            headless=_parse_bool(params.get("headless")),
        )
    else:
        return {
            "ok": False,
            "error": f"Commande web non supportée: {command}",
            "refresh_dashboard": False,
        }

    result["output"] = _format_market_action_output(command, result)
    result["refresh_dashboard"] = _market_action_has_useful_success(result)
    return result


def _content_type_for(path: Path) -> str:
    if path.suffix == ".css":
        return "text/css; charset=utf-8"
    if path.suffix == ".js":
        return "application/javascript; charset=utf-8"
    if path.suffix == ".json":
        return "application/json; charset=utf-8"
    return "text/html; charset=utf-8"


def _make_handler(data_dir: Path):
    class PortfolioWebHandler(BaseHTTPRequestHandler):
        server_version = "PortfolioTrackerWeb/0.1"

        def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
            body = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_text(self, text: str, *, status: int = 200) -> None:
            body = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _serve_static(self, name: str) -> None:
            path = STATIC_DIR / name
            if not path.exists():
                self._send_text("Not found", status=404)
                return
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", _content_type_for(path))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _serve_path(self, path: Path) -> None:
            if not path.exists():
                self._send_text("Not found", status=404)
                return
            body = path.read_bytes()
            content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition", f'inline; filename="{path.name}"')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._serve_static("v2.html")
                return
            if parsed.path == "/ged":
                self._serve_static("v2-ged.html")
                return
            if parsed.path == "/market":
                self._serve_static("v2-market.html")
                return
            if parsed.path.startswith("/documents/"):
                self._serve_static("v2-document.html")
                return
            if parsed.path.startswith("/contracts/"):
                self._serve_static("v2-contract.html")
                return
            if parsed.path.startswith("/supports/") or parsed.path.startswith("/v2/supports/"):
                self._serve_static("v2-support.html")
                return
            if parsed.path.startswith("/static/"):
                requested = parsed.path.removeprefix("/static/")
                if requested and "/" not in requested and requested != ".":
                    self._serve_static(requested)
                    return
            if parsed.path == "/api/dashboard":
                payload = build_v2_dashboard_data(data_dir, bootstrap=False)
                self._send_json(payload)
                return
            if parsed.path == "/api/ged":
                query = parse_qs(parsed.query)
                payload = build_v2_ged_data(
                    data_dir,
                    contract_name=(query.get("contract_name") or [None])[0] or None,
                    document_type=(query.get("document_type") or [None])[0] or None,
                    year=_optional_int((query.get("year") or [None])[0]),
                    status=(query.get("status") or [None])[0] or None,
                )
                self._send_json(payload)
                return
            if parsed.path == "/api/market":
                payload = build_v2_market_data(data_dir)
                self._send_json(payload)
                return
            if parsed.path == "/api/market/series":
                query = parse_qs(parsed.query)
                try:
                    payload = load_market_series(
                        data_dir,
                        kind=(query.get("kind") or [""])[0],
                        identifier=(query.get("id") or [""])[0],
                        date_from=(query.get("date_from") or [None])[0] or None,
                        date_to=(query.get("date_to") or [None])[0] or None,
                    )
                    self._send_json(payload)
                except KeyError as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=404)
                return
            if parsed.path.startswith("/api/documents/") and parsed.path.endswith("/arbitration-proposal"):
                document_id = parsed.path.removeprefix("/api/documents/").removesuffix("/arbitration-proposal")
                try:
                    payload = build_arbitration_proposal_for_document(data_dir, document_id)
                    self._send_json(payload)
                except (KeyError, ValueError) as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=500)
                return
            if parsed.path.startswith("/api/documents/") and not parsed.path.endswith("/file"):
                document_id = parsed.path.removeprefix("/api/documents/")
                try:
                    payload = build_v2_document_detail(data_dir, document_id)
                    self._send_json(payload)
                except KeyError:
                    self._send_json({"ok": False, "error": "Document introuvable"}, status=404)
                return
            if parsed.path.startswith("/api/documents/") and parsed.path.endswith("/structured-suggestions"):
                document_id = parsed.path.removeprefix("/api/documents/").removesuffix("/structured-suggestions")
                try:
                    with connect_v2_db(default_v2_db_path(data_dir)) as conn:
                        row = conn.execute(
                            "SELECT filepath, original_filename, document_type FROM documents WHERE document_id = ?",
                            (document_id,),
                        ).fetchone()
                    if row is None:
                        self._send_json({"ok": False, "error": "Document introuvable"}, status=404)
                        return
                    if str(row["document_type"]) != "structured_brochure":
                        self._send_json({"ok": False, "error": "Ce document n'est pas une brochure structurée"}, status=400)
                        return
                    from ..document_ingest import extract_pdf_text
                    pdf_path = (data_dir / str(row["filepath"])).resolve()
                    text, _ = extract_pdf_text(pdf_path)
                    result = extract_structured_brochure_suggestions(
                        text, filename=str(row["original_filename"] or ""),
                    )
                    result["ok"] = True
                    result["document_id"] = document_id
                    self._send_json(result)
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=500)
                return
            if parsed.path.startswith("/api/documents/") and parsed.path.endswith("/file"):
                document_id = parsed.path.removeprefix("/api/documents/").removesuffix("/file")
                with connect_v2_db(default_v2_db_path(data_dir)) as conn:
                    row = conn.execute(
                        "SELECT filepath FROM documents WHERE document_id = ?",
                        (document_id,),
                    ).fetchone()
                if row is None:
                    bootstrap_v2_data(data_dir)
                    with connect_v2_db(default_v2_db_path(data_dir)) as conn:
                        row = conn.execute(
                            "SELECT filepath FROM documents WHERE document_id = ?",
                            (document_id,),
                        ).fetchone()
                if row is None:
                    self._send_text("Not found", status=404)
                    return
                document_path = (data_dir / str(row["filepath"])).resolve()
                try:
                    document_path.relative_to(data_dir.resolve())
                except ValueError:
                    self._send_text("Forbidden", status=403)
                    return
                self._serve_path(document_path)
                return
            if parsed.path.startswith("/api/contracts/"):
                contract_id = parsed.path.removeprefix("/api/contracts/")
                try:
                    payload = build_v2_contract_detail(data_dir, contract_id)
                    self._send_json(payload)
                except KeyError:
                    self._send_json({"ok": False, "error": "Contrat introuvable"}, status=404)
                return
            if parsed.path.startswith("/api/supports/"):
                position_id = parsed.path.removeprefix("/api/supports/")
                try:
                    payload = build_v2_support_detail(data_dir, position_id)
                    self._send_json(payload)
                except KeyError:
                    self._send_json({"ok": False, "error": "Position introuvable"}, status=404)
                return
            if parsed.path == "/api/health":
                self._send_json({"ok": True, "timestamp": datetime.now().isoformat(timespec="seconds")})
                return
            self._send_text("Not found", status=404)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/api/documents/upload":
                try:
                    content_type = self.headers.get("Content-Type", "")
                    if "multipart/form-data" not in content_type:
                        self._send_json(
                            {"ok": False, "error": "Content-Type multipart/form-data attendu"},
                            status=400,
                        )
                        return
                    length = int(self.headers.get("Content-Length", "0") or "0")
                    raw_body = self.rfile.read(length) if length else b""
                    form = _parse_multipart_form_data(content_type, raw_body)
                    file_part = form.get("file")
                    if not isinstance(file_part, dict) or not file_part.get("filename"):
                        self._send_json({"ok": False, "error": "Champ file requis"}, status=400)
                        return
                    result = ingest_uploaded_document(
                        data_dir,
                        file_bytes=file_part.get("content") or b"",
                        original_filename=str(file_part["filename"]),
                        contract_name=str(form.get("contract_name") or "").strip() or None,
                        insurer=str(form.get("insurer") or "").strip() or None,
                        document_date=str(form.get("document_date") or "").strip() or None,
                        status=str(form.get("status") or "").strip() or None,
                        notes=str(form.get("notes") or "").strip() or None,
                    )
                    self._send_json(result, status=200 if result.get("ok") else 400)
                except ValueError as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=500)
                return
            if parsed.path == "/api/bootstrap":
                try:
                    result = bootstrap_v2_data(data_dir)
                    self._send_json(result)
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=500)
                return
            if parsed.path.startswith("/api/structured-rules/"):
                try:
                    length = int(self.headers.get("Content-Length", "0") or "0")
                    raw_body = self.rfile.read(length) if length else b"{}"
                    payload = json.loads(raw_body.decode("utf-8") or "{}")
                    asset_id = parsed.path.removeprefix("/api/structured-rules/")
                    result = save_structured_product_rule(data_dir, asset_id, payload)
                    self._send_json(result)
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=500)
                return
            if parsed.path.startswith("/api/structured-events/") and parsed.path.endswith("/validate"):
                try:
                    length = int(self.headers.get("Content-Length", "0") or "0")
                    raw_body = self.rfile.read(length) if length else b"{}"
                    payload = json.loads(raw_body.decode("utf-8") or "{}")
                    asset_id = parsed.path.removeprefix("/api/structured-events/").removesuffix("/validate")
                    result = save_structured_event_validation(data_dir, asset_id, payload)
                    self._send_json(result)
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=500)
                return
            if parsed.path.startswith("/api/documents/") and parsed.path.endswith("/validate"):
                try:
                    length = int(self.headers.get("Content-Length", "0") or "0")
                    raw_body = self.rfile.read(length) if length else b"{}"
                    payload = json.loads(raw_body.decode("utf-8") or "{}")
                    document_id = parsed.path.removeprefix("/api/documents/").removesuffix("/validate")
                    result = save_document_validation(data_dir, document_id, payload)
                    self._send_json(result)
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=500)
                return
            if parsed.path.startswith("/api/documents/") and parsed.path.endswith("/arbitration-apply"):
                try:
                    document_id = parsed.path.removeprefix("/api/documents/").removesuffix("/arbitration-apply")
                    result = apply_arbitration_proposal(data_dir, document_id)
                    self._send_json(result)
                except (KeyError, ValueError) as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=500)
                return
            if parsed.path.startswith("/api/documents/") and parsed.path.endswith("/arbitration-map"):
                try:
                    length = int(self.headers.get("Content-Length", "0") or "0")
                    raw_body = self.rfile.read(length) if length else b"{}"
                    payload = json.loads(raw_body.decode("utf-8") or "{}")
                    document_id = parsed.path.removeprefix("/api/documents/").removesuffix("/arbitration-map")
                    mappings = list(payload.get("mappings") or [])
                    result = save_arbitration_mappings(data_dir, document_id, mappings=mappings)
                    self._send_json(result)
                except (KeyError, ValueError) as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=500)
                return
            if parsed.path.startswith("/api/snapshots/") and parsed.path.endswith("/validate"):
                try:
                    length = int(self.headers.get("Content-Length", "0") or "0")
                    raw_body = self.rfile.read(length) if length else b"{}"
                    payload = json.loads(raw_body.decode("utf-8") or "{}")
                    snapshot_id = parsed.path.removeprefix("/api/snapshots/").removesuffix("/validate")
                    result = save_snapshot_validation(data_dir, snapshot_id, payload)
                    self._send_json(result)
                except (KeyError, ValueError) as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=500)
                return
            if parsed.path.startswith("/api/fonds-euro-pilotage/"):
                try:
                    length = int(self.headers.get("Content-Length", "0") or "0")
                    raw_body = self.rfile.read(length) if length else b"{}"
                    payload = json.loads(raw_body.decode("utf-8") or "{}")
                    contract_id = parsed.path.removeprefix("/api/fonds-euro-pilotage/")
                    result = save_fonds_euro_pilotage(data_dir, contract_id, payload)
                    self._send_json(result)
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=500)
                return
            if parsed.path == "/api/market/actions/update-uc-navs":
                try:
                    result = run_action(data_dir, "update-uc-navs", {"headless": True})
                    self._send_json(result, status=_market_action_http_status(result))
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=500)
                return
            if parsed.path == "/api/market/actions/update-underlyings":
                try:
                    result = run_action(data_dir, "update-underlyings", {"headless": True})
                    self._send_json(result, status=_market_action_http_status(result))
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=500)
                return
            if parsed.path == "/api/market/actions/backfill":
                try:
                    length = int(self.headers.get("Content-Length", "0") or "0")
                    raw_body = self.rfile.read(length) if length else b"{}"
                    payload = json.loads(raw_body.decode("utf-8") or "{}")
                    result = run_action(
                        data_dir,
                        "backfill-market-history",
                        {"years": payload.get("years"), "headless": True},
                    )
                    self._send_json(result, status=_market_action_http_status(result))
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=500)
                return
            if parsed.path == "/api/market/manual-point":
                try:
                    length = int(self.headers.get("Content-Length", "0") or "0")
                    raw_body = self.rfile.read(length) if length else b"{}"
                    payload = json.loads(raw_body.decode("utf-8") or "{}")
                    result = save_manual_market_point(
                        data_dir,
                        kind=payload.get("kind", ""),
                        identifier=payload.get("identifier", ""),
                        point_date=payload.get("date", ""),
                        value=float(payload.get("value", 0)),
                    )
                    self._send_json(result, status=200 if result.get("ok") else 400)
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=500)
                return
            if parsed.path == "/api/market/source-url":
                try:
                    length = int(self.headers.get("Content-Length", "0") or "0")
                    raw_body = self.rfile.read(length) if length else b"{}"
                    payload = json.loads(raw_body.decode("utf-8") or "{}")
                    result = save_market_source_url(
                        data_dir,
                        kind=payload.get("kind", ""),
                        identifier=payload.get("identifier", ""),
                        url=payload.get("url", ""),
                    )
                    self._send_json(result, status=200 if result.get("ok") else 400)
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=500)
                return
            self._send_text("Not found", status=404)

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

    return PortfolioWebHandler


def create_server(data_dir: Path, *, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    handler = _make_handler(Path(data_dir))
    return ThreadingHTTPServer((host, port), handler)


def run_web_app(data_dir: Path, *, host: str = "127.0.0.1", port: int = 8765) -> None:
    server = create_server(data_dir, host=host, port=port)
    print(f"✓ App web disponible sur http://{host}:{server.server_address[1]}")
    print("  Utilise Ctrl+C pour arrêter le serveur.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt du serveur web.")
    finally:
        server.server_close()
