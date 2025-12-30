"""
Main - Point d'entrée principal du Portfolio Tracker
"""
from pathlib import Path
from datetime import datetime

from .cli import PortfolioCLI


def main():
    """
    Point d'entrée principal.
    
    Peut être utilisé pour des scripts personnalisés ou des notebooks.
    Pour l'interface CLI, utiliser directement cli.py
    """
    # Exemple d'utilisation programmatique
    data_dir = Path("data")
    
    print("Portfolio Tracker")
    print("=" * 70)
    print()
    
    try:
        cli = PortfolioCLI(data_dir)
        
        # Afficher un résumé rapide
        print(f"Chargement du portefeuille depuis: {data_dir.absolute()}")
        print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print()
        
        # État global
        cli.status()
        
        # Alertes
        print("\nVérification des alertes...")
        cli.alerts()
        
    except FileNotFoundError as e:
        print(f"Erreur: {e}")
        print()
        print("Assurez-vous que les fichiers suivants existent:")
        print("  - data/assets.yaml")
        print("  - data/positions.yaml")
    except Exception as e:
        print(f"Erreur: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    main()









