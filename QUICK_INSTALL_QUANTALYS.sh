#!/bin/bash
# Script d'installation rapide de Playwright pour la récupération des notes Quantalys

echo "🚀 Installation de Playwright pour Portfolio Tracker"
echo "=" 
echo ""

# Couleurs
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Étape 1 : Installer Playwright
echo "📦 Étape 1/3 : Installation du module Playwright..."
pip3 install playwright
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓${NC} Module Playwright installé"
else
    echo -e "${RED}✗${NC} Erreur lors de l'installation de Playwright"
    exit 1
fi
echo ""

# Étape 2 : Installer Chromium
echo "🌐 Étape 2/3 : Installation de Chromium (cela peut prendre quelques minutes)..."
python3 -m playwright install chromium
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓${NC} Chromium installé"
else
    echo -e "${RED}✗${NC} Erreur lors de l'installation de Chromium"
    exit 1
fi
echo ""

# Étape 3 : Vérification
echo "🔍 Étape 3/3 : Vérification de l'installation..."
python3 -c "from playwright.sync_api import sync_playwright; print('OK')" 2>/dev/null
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓${NC} Installation réussie !"
else
    echo -e "${RED}✗${NC} Problème avec l'installation"
    exit 1
fi
echo ""

echo "=" 
echo -e "${GREEN}🎉 Installation terminée avec succès !${NC}"
echo ""
echo "📝 Prochaines étapes :"
echo "  1. Lancer la mise à jour des VL et notes :"
echo "     ${YELLOW}make update-navs${NC}"
echo ""
echo "  2. Voir les notes dans vos rapports :"
echo "     ${YELLOW}make himalia${NC}"
echo "     ${YELLOW}make swisslife${NC}"
echo ""
echo "💡 Les notes Quantalys seront désormais récupérées automatiquement"
echo "   à chaque fois que vous lancez 'make update-navs'"
echo ""




