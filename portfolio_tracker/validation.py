"""
Validation des fichiers de données du portfolio.
"""
from pathlib import Path
from typing import Any, Dict, List, Tuple
from enum import Enum
import yaml
from pydantic import ValidationError

from .schemas import (
    AssetSchema, PositionSchema, NavPointSchema,
    ValuationEventSchema
)


class ValidationSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class ValidationIssue:
    """Représente un problème de validation"""
    
    def __init__(
        self,
        severity: ValidationSeverity,
        location: str,
        field: str,
        message: str,
        context: Dict[str, Any] = None
    ):
        self.severity = severity
        self.location = location
        self.field = field
        self.message = message
        self.context = context or {}
    
    def __repr__(self) -> str:
        ctx = f" [{self.context}]" if self.context else ""
        return f"[{self.severity.value.upper()}] {self.location}.{self.field}: {self.message}{ctx}"


class ValidationReport:
    """Rapport de validation complet"""
    
    def __init__(self):
        self.issues: List[ValidationIssue] = []
    
    def add(self, issue: ValidationIssue):
        self.issues.append(issue)
    
    @property
    def errors(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == ValidationSeverity.ERROR]
    
    @property
    def warnings(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == ValidationSeverity.WARNING]
    
    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0
    
    def format_summary(self) -> str:
        """Retourne un résumé formaté (sans imprimer)"""
        lines = []
        lines.append("=" * 60)
        lines.append("RAPPORT DE VALIDATION")
        lines.append("=" * 60)
        
        if not self.issues:
            lines.append("✓ Aucun problème détecté")
            return "\n".join(lines)
        
        lines.append(f"Erreurs: {len(self.errors)}")
        lines.append(f"Warnings: {len(self.warnings)}")
        lines.append("")
        
        if self.errors:
            lines.append("ERREURS BLOQUANTES:")
            lines.append("-" * 60)
            for e in self.errors:
                lines.append(f"  {e}")
            lines.append("")
        
        if self.warnings:
            lines.append("WARNINGS:")
            lines.append("-" * 60)
            for w in self.warnings:
                lines.append(f"  {w}")
        
        lines.append("=" * 60)
        return "\n".join(lines)


def validate_assets_file(assets_file: Path) -> Tuple[List[AssetSchema], ValidationReport]:
    """
    Valide assets.yaml et retourne (assets_valides, rapport).
    En cas d'erreur, retourne ([], rapport_avec_erreurs).
    """
    report = ValidationReport()
    
    if not assets_file.exists():
        report.add(ValidationIssue(
            severity=ValidationSeverity.ERROR,
            location=str(assets_file),
            field="file",
            message="Fichier introuvable"
        ))
        return [], report
    
    try:
        with open(assets_file, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        report.add(ValidationIssue(
            severity=ValidationSeverity.ERROR,
            location=str(assets_file),
            field="yaml",
            message=f"Erreur parsing YAML: {e}"
        ))
        return [], report
    except Exception:
        raise
    
    if not data or 'assets' not in data:
        report.add(ValidationIssue(
            severity=ValidationSeverity.ERROR,
            location=str(assets_file),
            field="structure",
            message="Clé 'assets' manquante"
        ))
        return [], report
    
    assets_list = data['assets']
    if not isinstance(assets_list, list):
        report.add(ValidationIssue(
            severity=ValidationSeverity.ERROR,
            location=str(assets_file),
            field="assets",
            message="'assets' doit être une liste"
        ))
        return [], report
    
    validated_assets: List[AssetSchema] = []
    seen_ids = set()
    
    for idx, asset_data in enumerate(assets_list):
        location = f"assets.yaml:asset[{idx}]"
        
        if not isinstance(asset_data, dict):
            report.add(ValidationIssue(
                severity=ValidationSeverity.ERROR,
                location=location,
                field="structure",
                message="Asset doit être un dictionnaire"
            ))
            continue
        
        asset_id = asset_data.get('asset_id', f"<unknown_{idx}>")
        
        try:
            validated_data = {**asset_data}
            if 'type' in validated_data:
                validated_data['asset_type'] = validated_data.pop('type')
            
            asset = AssetSchema(**validated_data)
            
            if asset.asset_id in seen_ids:
                report.add(ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    location=location,
                    field="asset_id",
                    message=f"asset_id dupliqué: {asset.asset_id}"
                ))
                continue
            
            seen_ids.add(asset.asset_id)
            validated_assets.append(asset)
        
        except ValidationError as e:
            for error in e.errors():
                field_path = ".".join(str(loc) for loc in error['loc'])
                report.add(ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    location=location,
                    field=field_path,
                    message=error['msg'],
                    context={"asset_id": asset_id}
                ))
        except Exception:
            raise
    
    return validated_assets, report


def validate_positions_file(
    positions_file: Path,
    valid_asset_ids: set
) -> Tuple[List[PositionSchema], ValidationReport]:
    """
    Valide positions.yaml avec références aux assets.
    """
    report = ValidationReport()
    
    if not positions_file.exists():
        report.add(ValidationIssue(
            severity=ValidationSeverity.ERROR,
            location=str(positions_file),
            field="file",
            message="Fichier introuvable"
        ))
        return [], report
    
    try:
        with open(positions_file, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        report.add(ValidationIssue(
            severity=ValidationSeverity.ERROR,
            location=str(positions_file),
            field="yaml",
            message=f"Erreur parsing YAML: {e}"
        ))
        return [], report
    except Exception:
        raise
    
    if not data or 'positions' not in data:
        report.add(ValidationIssue(
            severity=ValidationSeverity.WARNING,
            location=str(positions_file),
            field="structure",
            message="Clé 'positions' manquante ou vide"
        ))
        return [], report
    
    positions_list = data['positions']
    if not isinstance(positions_list, list):
        report.add(ValidationIssue(
            severity=ValidationSeverity.ERROR,
            location=str(positions_file),
            field="positions",
            message="'positions' doit être une liste"
        ))
        return [], report
    
    validated_positions: List[PositionSchema] = []
    seen_ids = set()
    
    for idx, pos_data in enumerate(positions_list):
        location = f"positions.yaml:position[{idx}]"
        
        if not isinstance(pos_data, dict):
            report.add(ValidationIssue(
                severity=ValidationSeverity.ERROR,
                location=location,
                field="structure",
                message="Position doit être un dictionnaire"
            ))
            continue
        
        position_id = pos_data.get('position_id', f"<unknown_{idx}>")
        asset_id = pos_data.get('asset_id', '<unknown>')
        
        if asset_id not in valid_asset_ids:
            report.add(ValidationIssue(
                severity=ValidationSeverity.ERROR,
                location=location,
                field="asset_id",
                message=f"Référence asset_id invalide: {asset_id}",
                context={"position_id": position_id}
            ))
            continue
        
        try:
            validated_data = {**pos_data}
            if 'wrapper' in validated_data and isinstance(validated_data['wrapper'], dict):
                wrapper_data = validated_data['wrapper'].copy()
                if 'type' in wrapper_data:
                    wrapper_data['wrapper_type'] = wrapper_data.pop('type')
                validated_data['wrapper'] = wrapper_data
            
            position = PositionSchema(**validated_data)
            
            if position.position_id in seen_ids:
                report.add(ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    location=location,
                    field="position_id",
                    message=f"position_id dupliqué: {position.position_id}"
                ))
                continue
            
            seen_ids.add(position.position_id)
            validated_positions.append(position)
        
        except ValidationError as e:
            for error in e.errors():
                field_path = ".".join(str(loc) for loc in error['loc'])
                report.add(ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    location=location,
                    field=field_path,
                    message=error['msg'],
                    context={"position_id": position_id, "asset_id": asset_id}
                ))
        except Exception:
            raise
    
    return validated_positions, report


def validate_nav_history_file(nav_file: Path) -> Tuple[List[NavPointSchema], ValidationReport]:
    """Valide un fichier nav_*.yaml"""
    report = ValidationReport()
    
    if not nav_file.exists():
        return [], report
    
    try:
        with open(nav_file, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        report.add(ValidationIssue(
            severity=ValidationSeverity.ERROR,
            location=str(nav_file),
            field="yaml",
            message=f"Erreur parsing: {e}"
        ))
        return [], report
    except Exception:
        raise
    
    raw = data.get("nav_history") or []
    validated: List[NavPointSchema] = []
    
    for idx, entry in enumerate(raw):
        location = f"{nav_file.name}:nav_history[{idx}]"
        
        if not isinstance(entry, dict):
            report.add(ValidationIssue(
                severity=ValidationSeverity.WARNING,
                location=location,
                field="structure",
                message="Entrée ignorée (pas un dict)"
            ))
            continue
        
        try:
            validated_data = {**entry}
            if 'date' in validated_data:
                validated_data['point_date'] = validated_data.pop('date')
            
            point = NavPointSchema(**validated_data)
            validated.append(point)
        except ValidationError as e:
            for error in e.errors():
                field_path = ".".join(str(loc) for loc in error['loc'])
                report.add(ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    location=location,
                    field=field_path,
                    message=f"Entrée ignorée: {error['msg']}",
                    context={"date": entry.get("date")}
                ))
        except Exception:
            raise
    
    return validated, report

