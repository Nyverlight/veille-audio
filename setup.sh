#!/usr/bin/env bash
# setup.sh - execute par la routine AVANT la session (installation des deps).
set -e
echo "== Installation des dependances Python =="
pip install --quiet -r requirements.txt
echo "== Dependances installees =="
