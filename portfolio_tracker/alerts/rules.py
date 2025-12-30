"""
Rules - Définition et gestion des règles d'alertes
"""
from dataclasses import dataclass
from typing import List, Callable, Any, Optional
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path

from ..core.portfolio import Portfolio
from ..core.asset import Asset, AssetType
from ..core.position import Position
from ..valuation.base import ValuationResult
from ..market.rates import RatesProvider
from ..market.nav import NAVProvider


class AlertSeverity(Enum):
    """Niveau de sévérité d'une alerte"""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class AlertTrigger:
    """
    Représente une alerte déclenchée.
    """
    rule_name: str
    severity: AlertSeverity
    message: str
    asset_id: Optional[str] = None
    position_id: Optional[str] = None
    trigger_date: date = None
    metadata: dict = None
    
    def __post_init__(self):
        if self.trigger_date is None:
            self.trigger_date = datetime.now().date()
        if self.metadata is None:
            self.metadata = {}
    
    def __repr__(self) -> str:
        return f"[{self.severity.value.upper()}] {self.rule_name}: {self.message}"


class AlertRule:
    """
    Règle d'alerte abstraite.
    
    Une règle définit une condition à surveiller et génère
    des AlertTrigger quand la condition est remplie.
    """
    
    def __init__(self, name: str, severity: AlertSeverity):
        """
        Initialise une règle d'alerte.
        
        Args:
            name: Nom de la règle
            severity: Niveau de sévérité
        """
        self.name = name
        self.severity = severity
    
    def check(
        self, 
        portfolio: Portfolio,
        market_data_dir: Any
    ) -> List[AlertTrigger]:
        """
        Vérifie la règle sur le portefeuille.
        
        Args:
            portfolio: Portefeuille à vérifier
            market_data_dir: Répertoire des données de marché
        
        Returns:
            Liste des alertes déclenchées
        """
        raise NotImplementedError()


class DataFreshnessRule(AlertRule):
    """Alerte si des données de marché sont trop anciennes"""
    
    def __init__(self, max_days: int = 7):
        super().__init__(
            name=f"data_freshness_{max_days}d",
            severity=AlertSeverity.WARNING
        )
        self.max_days = max_days
    
    def check(self, portfolio: Portfolio, market_data_dir: Any) -> List[AlertTrigger]:
        """Vérifie la fraîcheur des données de marché"""
        triggers = []
        nav_provider = NAVProvider(market_data_dir)
        threshold_date = datetime.now().date() - timedelta(days=self.max_days)
        
        # Vérifier les UC cotées
        uc_assets = portfolio.get_assets_by_type(AssetType.UC_FUND)
        for asset in uc_assets:
            if not nav_provider.is_data_available(asset.asset_id):
                triggers.append(AlertTrigger(
                    rule_name=self.name,
                    severity=self.severity,
                    message=f"Aucune VL disponible pour {asset.name}",
                    asset_id=asset.asset_id
                ))
                continue
            
            latest_date = nav_provider.get_latest_date(asset.asset_id)
            if latest_date and latest_date < threshold_date:
                days_old = (datetime.now().date() - latest_date).days
                triggers.append(AlertTrigger(
                    rule_name=self.name,
                    severity=self.severity,
                    message=f"VL de {asset.name} datée de {days_old} jours",
                    asset_id=asset.asset_id,
                    metadata={'last_update': latest_date.isoformat(), 'days_old': days_old}
                ))
        
        return triggers


class StructuredProductObservationRule(AlertRule):
    """Alerte si une date d'observation approche pour un produit structuré"""
    
    def __init__(self, days_before: int = 7):
        super().__init__(
            name=f"structured_observation_{days_before}d",
            severity=AlertSeverity.INFO
        )
        self.days_before = days_before
    
    def check(self, portfolio: Portfolio, market_data_dir: Any) -> List[AlertTrigger]:
        """Vérifie les dates d'observation des produits structurés"""
        triggers = []
        today = datetime.now().date()
        
        structured_assets = portfolio.get_assets_by_type(AssetType.STRUCTURED_PRODUCT)
        
        for asset in structured_assets:
            positions = portfolio.get_positions_by_asset(asset.asset_id)
            
            for position in positions:
                # Calculer la prochaine date d'observation
                metadata = asset.metadata or {}
                period_months = metadata.get('period_months', 12)
                
                subscription_date = position.investment.subscription_date
                next_obs_date = self._calculate_next_observation(
                    subscription_date,
                    today,
                    period_months
                )
                
                if next_obs_date:
                    days_until = (next_obs_date - today).days
                    if 0 <= days_until <= self.days_before:
                        triggers.append(AlertTrigger(
                            rule_name=self.name,
                            severity=self.severity,
                            message=f"Observation de {asset.name} dans {days_until} jours",
                            asset_id=asset.asset_id,
                            position_id=position.position_id,
                            metadata={'observation_date': next_obs_date.isoformat()}
                        ))
        
        return triggers
    
    def _calculate_next_observation(
        self, 
        subscription_date: date, 
        today: date, 
        period_months: int
    ) -> Optional[date]:
        """Calcule la prochaine date d'observation"""
        from dateutil.relativedelta import relativedelta
        
        current_date = subscription_date
        while current_date <= today:
            current_date = current_date + relativedelta(months=period_months)
        
        return current_date if current_date > today else None


class UnderlyingThresholdRule(AlertRule):
    """Alerte si un sous-jacent approche d'un seuil critique"""
    
    def __init__(self, threshold_percent: float = 5.0):
        super().__init__(
            name=f"underlying_threshold_{threshold_percent}pct",
            severity=AlertSeverity.WARNING
        )
        self.threshold_percent = threshold_percent
    
    def check(self, portfolio: Portfolio, market_data_dir: Any) -> List[AlertTrigger]:
        """Vérifie si les sous-jacents approchent de seuils"""
        triggers = []
        rates_provider = RatesProvider(market_data_dir)
        
        structured_assets = portfolio.get_assets_by_type(AssetType.STRUCTURED_PRODUCT)
        
        for asset in structured_assets:
            metadata = asset.metadata or {}
            underlying = metadata.get('underlying')
            barrier = metadata.get('barrier')
            
            if not underlying or not barrier:
                continue
            
            # Récupérer le niveau actuel du sous-jacent
            current_data = rates_provider.get_data(underlying)
            if not current_data:
                triggers.append(AlertTrigger(
                    rule_name=self.name,
                    severity=AlertSeverity.WARNING,
                    message=f"Impossible de récupérer le niveau de {underlying}",
                    asset_id=asset.asset_id
                ))
                continue
            
            current_level = current_data['value']
            distance_to_barrier = ((current_level / barrier) - 1) * 100
            
            if abs(distance_to_barrier) <= self.threshold_percent:
                triggers.append(AlertTrigger(
                    rule_name=self.name,
                    severity=self.severity,
                    message=f"{asset.name}: {underlying} à {distance_to_barrier:.2f}% de la barrière",
                    asset_id=asset.asset_id,
                    metadata={
                        'underlying': underlying,
                        'current_level': current_level,
                        'barrier': barrier,
                        'distance_pct': distance_to_barrier
                    }
                ))
        
        return triggers


class MissingValuationRule(AlertRule):
    """Alerte si une position ne peut pas être valorisée"""
    
    def __init__(self):
        super().__init__(
            name="missing_valuation",
            severity=AlertSeverity.ERROR
        )
    
    def check(self, portfolio: Portfolio, market_data_dir: Any) -> List[AlertTrigger]:
        """Vérifie que toutes les positions peuvent être valorisées"""
        from ..valuation import EventBasedEngine, DeclarativeEngine, MarkToMarketEngine, HybridEngine
        from ..core.asset import ValuationEngine
        
        triggers = []
        
        # Mapper les engines
        engines = {
            ValuationEngine.EVENT_BASED: EventBasedEngine(portfolio.data_dir),
            ValuationEngine.DECLARATIVE: DeclarativeEngine(portfolio.data_dir),
            ValuationEngine.MARK_TO_MARKET: MarkToMarketEngine(portfolio.data_dir),
            ValuationEngine.HYBRID: HybridEngine(portfolio.data_dir),
        }
        
        for position in portfolio.list_all_positions():
            asset = portfolio.get_asset(position.asset_id)
            if not asset:
                triggers.append(AlertTrigger(
                    rule_name=self.name,
                    severity=self.severity,
                    message=f"Asset {position.asset_id} introuvable",
                    position_id=position.position_id
                ))
                continue
            
            engine = engines.get(asset.valuation_engine)
            if not engine:
                triggers.append(AlertTrigger(
                    rule_name=self.name,
                    severity=self.severity,
                    message=f"Engine {asset.valuation_engine} non disponible",
                    asset_id=asset.asset_id,
                    position_id=position.position_id
                ))
                continue
            
            # Essayer de valoriser
            result = engine.valuate(asset, position)
            if result.status == "error":
                triggers.append(AlertTrigger(
                    rule_name=self.name,
                    severity=self.severity,
                    message=f"{asset.name}: {result.message}",
                    asset_id=asset.asset_id,
                    position_id=position.position_id
                ))
        
        return triggers


class ExpectedPaymentMissingRule(AlertRule):
    """
    Alerte si un paiement attendu (coupon / autocall / échéance) est passé
    mais n'a pas été saisi dans events_<asset_id>.yaml.
    """

    def __init__(self, grace_days: int = 7):
        super().__init__(
            name=f"expected_payment_missing_{grace_days}d",
            severity=AlertSeverity.WARNING
        )
        self.grace_days = grace_days

    def check(self, portfolio: Portfolio, market_data_dir: Any) -> List[AlertTrigger]:
        import yaml

        triggers: List[AlertTrigger] = []
        today = datetime.now().date()
        cutoff = today - timedelta(days=self.grace_days)

        structured_assets = portfolio.get_assets_by_type(AssetType.STRUCTURED_PRODUCT)

        def is_expected_payment_type(t: str) -> bool:
            tt = (t or "").lower()
            return (
                tt.endswith("_expected")
                or tt.endswith("_payment_expected")
                or tt in {"maturity_expected", "maturity_payment_expected"}
            )

        def expected_to_real_type(t: str) -> Optional[str]:
            mapping = {
                "coupon_expected": "coupon",
                "autocall_payment_expected": "autocall",
                "maturity_payment_expected": "maturity",
                "maturity_expected": "maturity",
            }
            return mapping.get((t or "").lower())

        for asset in structured_assets:
            events_file = Path(market_data_dir) / f"events_{asset.asset_id}.yaml"
            if not events_file.exists():
                continue

            try:
                data = yaml.safe_load(events_file.read_text(encoding='utf-8'))
            except Exception:
                continue

            if not isinstance(data, dict):
                continue

            real = data.get("events") or []
            expected = data.get("expected_events") or []

            # Indexer les dates réelles par type
            real_dates_by_type = {}
            for e in real:
                if not isinstance(e, dict):
                    continue
                et = (e.get("type") or "").lower()
                ds = e.get("date")
                if not et or not ds:
                    continue
                try:
                    d = datetime.fromisoformat(str(ds)).date()
                except Exception:
                    continue
                real_dates_by_type.setdefault(et, []).append(d)

            for ee in expected:
                if not isinstance(ee, dict):
                    continue
                et = ee.get("type")
                ds = ee.get("date")
                if not et or not ds or not is_expected_payment_type(et):
                    continue
                try:
                    expected_date = datetime.fromisoformat(str(ds)).date()
                except Exception:
                    continue
                if expected_date >= cutoff:
                    continue

                real_type = expected_to_real_type(et)
                if not real_type:
                    continue

                # tolérance +/- 7 jours autour de la date attendue
                matched = any(
                    abs((d - expected_date).days) <= 7
                    for d in real_dates_by_type.get(real_type, [])
                )
                if matched:
                    continue

                triggers.append(
                    AlertTrigger(
                        rule_name=self.name,
                        severity=self.severity,
                        message=f"Paiement attendu non saisi: {asset.name} ({et}) du {expected_date.isoformat()}",
                        asset_id=asset.asset_id,
                        metadata={
                            "expected_type": et,
                            "expected_date": expected_date.isoformat(),
                        },
                    )
                )

        return triggers


class AlertManager:
    """
    Gestionnaire d'alertes.
    
    Enregistre des règles et les vérifie sur le portefeuille.
    """
    
    def __init__(self, portfolio: Portfolio, market_data_dir: Any):
        """
        Initialise le gestionnaire d'alertes.
        
        Args:
            portfolio: Portefeuille à surveiller
            market_data_dir: Répertoire des données de marché
        """
        self.portfolio = portfolio
        self.market_data_dir = market_data_dir
        self.rules: List[AlertRule] = []
    
    def add_rule(self, rule: AlertRule):
        """Ajoute une règle d'alerte"""
        self.rules.append(rule)
    
    def add_default_rules(self):
        """Ajoute un ensemble de règles par défaut"""
        self.rules.extend([
            DataFreshnessRule(max_days=7),
            StructuredProductObservationRule(days_before=7),
            ExpectedPaymentMissingRule(grace_days=7),
            UnderlyingThresholdRule(threshold_percent=5.0),
            MissingValuationRule(),
        ])
    
    def check_all(self) -> List[AlertTrigger]:
        """
        Vérifie toutes les règles.
        
        Returns:
            Liste de toutes les alertes déclenchées
        """
        all_triggers = []
        
        for rule in self.rules:
            try:
                triggers = rule.check(self.portfolio, self.market_data_dir)
                all_triggers.extend(triggers)
            except Exception as e:
                # Ne pas faire échouer le check complet si une règle échoue
                all_triggers.append(AlertTrigger(
                    rule_name=rule.name,
                    severity=AlertSeverity.ERROR,
                    message=f"Erreur lors de la vérification: {str(e)}"
                ))
        
        return all_triggers
    
    def check_by_severity(self, severity: AlertSeverity) -> List[AlertTrigger]:
        """Récupère uniquement les alertes d'une sévérité donnée"""
        all_triggers = self.check_all()
        return [t for t in all_triggers if t.severity == severity]



