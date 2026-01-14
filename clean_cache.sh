#!/bin/bash
# Nettoyer tous les caches Python
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find . -type f -name "*.pyc" -delete 2>/dev/null || true
find . -type f -name "*.pyo" -delete 2>/dev/null || true
find .venv -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find .venv -type f -name "*.pyc" -delete 2>/dev/null || true
echo "✓ Cache Python nettoyé"
