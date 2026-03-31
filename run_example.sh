#!/bin/bash
# Démonstration minimale — CLI V2

set -e
DATA_DIR="portfolio_tracker/data"

echo "========================================================================"
echo "Portfolio Tracker — démonstration (CLI V2)"
echo "========================================================================"
echo ""

echo "1. Synthèse (global / status)"
echo "------------------------------------------------------------------------"
python3 -m portfolio_tracker.cli --data-dir "$DATA_DIR" status
echo ""

echo "2. Produits structurés"
echo "------------------------------------------------------------------------"
python3 -m portfolio_tracker.cli --data-dir "$DATA_DIR" structured
echo ""

echo "========================================================================"
echo "Terminé. Suite : make web  ou  voir README.md"
echo "========================================================================"
