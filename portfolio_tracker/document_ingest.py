"""Ingestion documentaire V2: upload, classification et indexation YAML."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import re
from pathlib import Path
from typing import Any, Callable

from pypdf import PdfReader
import yaml

try:
    import pdfplumber
except ImportError:  # pragma: no cover - dépendance optionnelle au runtime
    pdfplumber = None


DocumentClassifier = Callable[[dict[str, Any]], dict[str, Any]]


DOCUMENT_TYPE_LABELS = {
    "arbitration_letter": "arbitrage",
    "insurer_statement": "releve",
    "insurer_movement_list": "mouvements",
    "structured_brochure": "brochure",
    "contract_document": "contractuel",
    "insurer_letter": "autre",
}


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )


def _slug(value: str) -> str:
    value = value.strip().lower()
    value = (
        value.replace("é", "e")
        .replace("è", "e")
        .replace("ê", "e")
        .replace("ë", "e")
        .replace("à", "a")
        .replace("â", "a")
        .replace("ä", "a")
        .replace("î", "i")
        .replace("ï", "i")
        .replace("ô", "o")
        .replace("ö", "o")
        .replace("ù", "u")
        .replace("û", "u")
        .replace("ü", "u")
        .replace("ç", "c")
    )
    return re.sub(r"[^a-z0-9]+", "_", value).strip("_")


def _extract_text_with_pdfplumber(pdf_path: Path) -> str:
    if pdfplumber is None:
        return ""
    pages: list[str] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    return "\n".join(pages).strip()


def _extract_text_with_pypdf(pdf_path: Path) -> str:
    reader = PdfReader(str(pdf_path))
    return "\n".join((page.extract_text() or "") for page in reader.pages).strip()


def extract_pdf_text(pdf_path: Path) -> tuple[str, str]:
    try:
        text = _extract_text_with_pdfplumber(pdf_path)
        if text:
            return text, "pdfplumber"
    except Exception:
        pass

    try:
        text = _extract_text_with_pypdf(pdf_path)
        if text:
            return text, "pypdf"
    except Exception:
        pass

    return "", "unavailable"


def _discover_contract_configs(data_dir: Path) -> dict[str, dict[str, Any]]:
    base_dir = Path(data_dir) / "documents" / "insurer"
    configs: dict[str, dict[str, Any]] = {}

    for statement_index in sorted(base_dir.glob("**/index.yaml")):
        if statement_index.parent.name == "courriers":
            continue
        payload = _load_yaml(statement_index)
        documents = payload.get("documents") or []
        if not documents:
            continue
        sample = documents[0]
        contract_name = str(sample.get("contract_name") or "").strip()
        insurer = str(sample.get("insurer") or "").strip()
        if not contract_name or not insurer:
            continue
        root_relative = statement_index.parent.relative_to(data_dir)
        root_path = Path(root_relative)
        insurer_slug = _slug(insurer)
        configs[contract_name] = {
            "contract_name": contract_name,
            "insurer": insurer,
            "insurer_slug": insurer_slug,
            "root_path": root_path,
            "statement_index": root_path / "index.yaml",
            "correspondence_index": root_path / "courriers" / "index.yaml",
        }
    return configs


def _find_contract_config(
    data_dir: Path,
    *,
    contract_name: str | None,
    insurer: str | None,
) -> dict[str, Any] | None:
    configs = _discover_contract_configs(data_dir)
    if contract_name:
        return configs.get(contract_name)

    if insurer:
        matches = [cfg for cfg in configs.values() if _slug(str(cfg["insurer"])) == _slug(insurer)]
        if len(matches) == 1:
            return matches[0]
    return None


def _infer_coverage_year(filename: str, text: str) -> int | None:
    """Try to infer the coverage year from a statement PDF (31/12/YYYY patterns)."""
    blob = f"{filename}\n{text}"
    matches = re.findall(r"31[/.]12[/.](\d{4})", blob)
    if matches:
        plausible = {int(y) for y in matches if 2015 <= int(y) <= 2035}
        if len(plausible) == 1:
            return plausible.pop()
        if plausible:
            return max(plausible)
    dec_matches = re.findall(r"d[eé]cembre\s+(\d{4})", blob, re.IGNORECASE)
    if dec_matches:
        plausible = {int(y) for y in dec_matches if 2015 <= int(y) <= 2035}
        if len(plausible) == 1:
            return plausible.pop()
    return None


def _heuristic_document_type(filename: str, text: str) -> dict[str, Any]:
    blob = f"{filename}\n{text}".lower()
    if "arbitrage" in blob:
        return {"document_type": "arbitration_letter", "confidence": 0.95, "reason": "Mot-clé arbitrage détecté."}
    if "releve de situation" in blob or "relevé de situation" in blob:
        result: dict[str, Any] = {"document_type": "insurer_statement", "confidence": 0.95, "reason": "Mot-clé relevé de situation détecté."}
        coverage_year = _infer_coverage_year(filename, text)
        if coverage_year is not None:
            result["coverage_year"] = coverage_year
        return result
    if ("brochure" in blob or "coupon" in blob or "barriere" in blob or "barrière" in blob) and re.search(
        r"\b(?:fr|lu)[a-z0-9]{10}\b", blob
    ):
        return {"document_type": "structured_brochure", "confidence": 0.9, "reason": "Signature de brochure structurée détectée."}
    if any(token in blob for token in ("historique des mouvements", "liste des mouvements", "liste des operations", "liste des opérations")):
        return {"document_type": "insurer_movement_list", "confidence": 0.85, "reason": "Mot-clé liste de mouvements détecté."}
    if any(token in blob for token in ("dispositions particulieres", "dispositions particulières", "garanties", "conditions")):
        return {"document_type": "contract_document", "confidence": 0.7, "reason": "Document contractuel probable."}
    return {"document_type": "insurer_letter", "confidence": 0.4, "reason": "Aucun motif fort, classement par défaut en autre courrier."}


def classify_document(
    *,
    filename: str,
    text: str,
    classifier: DocumentClassifier | None = None,
) -> dict[str, Any]:
    if classifier is not None:
        payload = classifier({"filename": filename, "text": text}) or {}
        payload.setdefault("source", "custom")
        return payload

    payload = _heuristic_document_type(filename, text)
    payload["source"] = "heuristic"
    return payload


def _ensure_iso_date(value: str | None, *, fallback: datetime) -> str:
    if value:
        raw = str(value).strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            return raw
    return fallback.date().isoformat()


def _sanitize_filename(filename: str) -> str:
    stem = Path(filename).stem
    suffix = Path(filename).suffix.lower() or ".pdf"
    safe_stem = _slug(stem) or "document"
    return f"{safe_stem}{suffix}"


def _unique_relative_path(data_dir: Path, relative_path: Path) -> Path:
    candidate = Path(relative_path)
    absolute = Path(data_dir) / candidate
    if not absolute.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    parent = candidate.parent
    index = 2
    while True:
        next_candidate = parent / f"{stem}_{index}{suffix}"
        if not (Path(data_dir) / next_candidate).exists():
            return next_candidate
        index += 1


def _next_document_id(documents: list[dict[str, Any]], base_id: str) -> str:
    existing = {str(doc.get("document_id") or "") for doc in documents}
    if base_id not in existing:
        return base_id
    index = 2
    while f"{base_id}_{index}" in existing:
        index += 1
    return f"{base_id}_{index}"


def _find_duplicate_by_sha(data_dir: Path, sha_value: str) -> dict[str, Any] | None:
    for index_path in sorted(Path(data_dir).glob("**/index.yaml")):
        payload = _load_yaml(index_path)
        for document in payload.get("documents") or []:
            if str(document.get("sha256") or "") == sha_value:
                return document
    return None


def _build_storage_plan(
    *,
    data_dir: Path,
    contract_config: dict[str, Any] | None,
    document_type: str,
    document_date: str,
    original_filename: str,
    insurer: str | None,
) -> tuple[Path, Path]:
    if document_type == "structured_brochure":
        target_dir = Path("product_brochure")
        index_path = target_dir / "index.yaml"
        filename = _sanitize_filename(original_filename)
        return _unique_relative_path(data_dir, target_dir / filename), index_path

    if contract_config is None:
        raise ValueError("contract_name requis pour ce type de document")

    insurer_slug = str(contract_config["insurer_slug"])
    root_path = Path(contract_config["root_path"])
    if document_type == "insurer_statement":
        relative = root_path / "releves" / f"{document_date}_releve_situation_{insurer_slug}.pdf"
        return _unique_relative_path(data_dir, relative), Path(contract_config["statement_index"])
    if document_type == "arbitration_letter":
        relative = root_path / "courriers" / "arbitrages" / f"{document_date}_arbitrage_{insurer_slug}.pdf"
        return _unique_relative_path(data_dir, relative), Path(contract_config["correspondence_index"])
    if document_type == "insurer_movement_list":
        relative = root_path / "courriers" / "mouvements" / f"{document_date}_mouvements_{insurer_slug}.pdf"
        return _unique_relative_path(data_dir, relative), Path(contract_config["correspondence_index"])
    if document_type == "contract_document":
        relative = root_path / "courriers" / "contractuel" / f"{document_date}_{_sanitize_filename(original_filename)}"
        return _unique_relative_path(data_dir, relative), Path(contract_config["correspondence_index"])

    other_slug = _slug(insurer or contract_config["insurer"] or "courrier") or "courrier"
    relative = root_path / "courriers" / "a_classer" / f"{document_date}_{other_slug}_{_sanitize_filename(original_filename)}"
    return _unique_relative_path(data_dir, relative), Path(contract_config["correspondence_index"])


def _append_document_to_index(index_path: Path, document: dict[str, Any]) -> dict[str, Any]:
    payload = _load_yaml(index_path)
    documents = list(payload.get("documents") or [])
    documents.append(document)
    documents.sort(
        key=lambda row: (
            str(row.get("document_date") or row.get("statement_date") or ""),
            str(row.get("document_id") or ""),
        )
    )
    payload["documents"] = documents
    _write_yaml(index_path, payload)
    return payload


def ingest_uploaded_document(
    data_dir: Path,
    *,
    file_bytes: bytes,
    original_filename: str,
    contract_name: str | None = None,
    insurer: str | None = None,
    document_date: str | None = None,
    status: str | None = None,
    notes: str | None = None,
    classifier: DocumentClassifier | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    data_dir = Path(data_dir)
    if not original_filename.lower().endswith(".pdf"):
        raise ValueError("Seuls les PDF sont supportés pour le moment")

    now = now or datetime.now()
    sha_value = sha256(file_bytes).hexdigest()
    duplicate = _find_duplicate_by_sha(data_dir, sha_value)
    if duplicate is not None:
        return {
            "ok": True,
            "duplicate": True,
            "document": duplicate,
            "classification": {
                "document_type": duplicate.get("document_type"),
                "label": DOCUMENT_TYPE_LABELS.get(str(duplicate.get("document_type") or ""), "autre"),
                "source": "existing_index",
            },
        }

    temp_dir = data_dir / "tmp" / "uploads"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_pdf = temp_dir / f"{sha_value}.pdf"
    temp_pdf.write_bytes(file_bytes)

    try:
        extracted_text, extraction_method = extract_pdf_text(temp_pdf)
        classification = classify_document(filename=original_filename, text=extracted_text, classifier=classifier)
        final_type = str(classification.get("document_type") or "insurer_letter")
        final_insurer = insurer
        contract_config = _find_contract_config(data_dir, contract_name=contract_name, insurer=insurer)
        if contract_config is not None:
            contract_name = str(contract_config["contract_name"])
            final_insurer = str(contract_config["insurer"])
        final_date = _ensure_iso_date(
            document_date or classification.get("document_date"),
            fallback=now,
        )

        target_relative_path, index_relative_path = _build_storage_plan(
            data_dir=data_dir,
            contract_config=contract_config,
            document_type=final_type,
            document_date=final_date,
            original_filename=original_filename,
            insurer=final_insurer,
        )
        target_path = data_dir / target_relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(file_bytes)

        index_path = data_dir / index_relative_path
        index_payload = _load_yaml(index_path)
        documents = list(index_payload.get("documents") or [])
        date_slug = final_date.replace("-", "_")
        document_id_prefix = _slug(final_insurer or "document")
        type_slug_map = {
            "arbitration_letter": "arbitrage",
            "insurer_statement": "releve_situation",
            "insurer_movement_list": "mouvements",
            "structured_brochure": "structured_brochure",
            "contract_document": "document_contractuel",
            "insurer_letter": "courrier",
        }
        base_id = f"{document_id_prefix}_{type_slug_map.get(final_type, 'document')}_{date_slug}"
        document_id = _next_document_id(documents, base_id)

        document_entry = {
            "document_id": document_id,
            "document_type": final_type,
            "insurer": final_insurer or "Unknown",
            "contract_name": contract_name,
            "status": status or "active",
            "filepath": str(target_relative_path),
            "original_filename": original_filename,
            "sha256": sha_value,
            "notes": (
                notes
                or f"Document importé automatiquement ({classification.get('source', 'heuristic')}, {classification.get('reason', '').strip() or 'sans détail'})."
            ),
        }
        if final_type == "insurer_statement":
            document_entry["statement_date"] = final_date
        else:
            document_entry["document_date"] = final_date

        coverage_year = classification.get("coverage_year")
        if coverage_year not in (None, ""):
            document_entry["coverage_year"] = int(coverage_year)

        _append_document_to_index(index_path, document_entry)

        from .document_extractors import run_post_ingest_hooks
        extraction = run_post_ingest_hooks(data_dir, document_entry, extracted_text)

        return {
            "ok": True,
            "duplicate": False,
            "document": document_entry,
            "classification": {
                "document_type": final_type,
                "label": DOCUMENT_TYPE_LABELS.get(final_type, "autre"),
                "confidence": classification.get("confidence"),
                "reason": classification.get("reason"),
                "source": classification.get("source"),
            },
            "storage": {
                "path": str(target_relative_path),
                "index_path": str(index_relative_path),
                "extraction_method": extraction_method,
            },
            "extraction": extraction,
        }
    finally:
        if temp_pdf.exists():
            temp_pdf.unlink()
