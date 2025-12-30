"""
Exceptions métier pour le portfolio tracker.
"""


class PortfolioDataError(Exception):
    """Erreur corruption données (bloquante)"""
    pass


class PortfolioValidationError(Exception):
    """Erreur validation schéma (bloquante)"""
    pass


class PortfolioReferenceError(Exception):
    """Référence invalide (bloquante)"""
    pass


class ValuationDataWarning(Warning):
    """Warning valorisation dégradée (non bloquant)"""
    pass

