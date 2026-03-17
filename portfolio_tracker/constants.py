"""
Constantes nommées — remplace les valeurs magiques parsemées dans le code.
"""

# Seuil pour considérer une position comme vendue (units_held ≈ 0)
POSITION_SOLD_THRESHOLD = 0.01

# Seuil d'unités pour détecter une liquidation via lot 'tax'
LIQUIDATION_UNITS_THRESHOLD = -10

# Date de participation aux bénéfices (mois, jour)
BENEFIT_DATE = (12, 31)
