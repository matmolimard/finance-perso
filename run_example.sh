#!/bin/bash

# Script de démonstration du Portfolio Tracker
# Utilise les données d'exemple fournies

echo "========================================================================"
echo "Portfolio Tracker - Démonstration"
echo "========================================================================"
echo ""

DATA_DIR="portfolio_tracker/data"

echo "📊 1. État global du portefeuille"
echo "------------------------------------------------------------------------"
python3 -m portfolio_tracker.cli --data-dir "$DATA_DIR" status
echo ""

echo ""
echo "📁 2. Vue par enveloppe"
echo "------------------------------------------------------------------------"
python3 -m portfolio_tracker.cli --data-dir "$DATA_DIR" wrapper
echo ""

echo ""
echo "📂 3. Vue par type d'actif"
echo "------------------------------------------------------------------------"
python3 -m portfolio_tracker.cli --data-dir "$DATA_DIR" type
echo ""

echo ""
echo "🔔 4. Alertes"
echo "------------------------------------------------------------------------"
python3 -m portfolio_tracker.cli --data-dir "$DATA_DIR" alerts
echo ""

echo ""
echo "========================================================================"
echo "Démonstration terminée"
echo "========================================================================"
echo ""
echo "Pour plus d'informations :"
echo "  - README.md : Documentation complète"
echo "  - USAGE.md : Guide d'utilisation détaillé"
echo "  - QUICKSTART.md : Démarrage rapide avec vos données"
echo ""









