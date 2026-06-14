#!/usr/bin/env bash
# setup.sh - execute par la routine AVANT la session (installation des deps).
set -e

echo "== Mise a jour de la chaine pip (corrige les builds type sgmllib3k) =="
pip install --quiet --upgrade pip setuptools wheel

echo "== Installation des dependances Python =="
pip install --quiet -r requirements.txt

echo "== Injection du CA du proxy dans le bundle certifi (pour edge-tts) =="
# edge-tts code en dur certifi et ignore SSL_CERT_FILE / REQUESTS_CA_BUNDLE.
# Derriere le proxy d'inspection TLS du bac a sable cloud, son CA n'est pas
# reconnu -> on ajoute les CA systeme (qui contiennent ce CA) au bundle certifi.
CERTIFI_PEM="$(python -c 'import certifi; print(certifi.where())' 2>/dev/null || true)"
if [ -n "$CERTIFI_PEM" ] && [ -f "$CERTIFI_PEM" ] && [ -w "$CERTIFI_PEM" ]; then
  added=0
  for ca in /usr/local/share/ca-certificates/*.crt /etc/ssl/certs/ca-certificates.crt; do
    if [ -f "$ca" ]; then
      cat "$ca" >> "$CERTIFI_PEM"
      added=1
    fi
  done
  if [ "$added" = "1" ]; then
    echo "   CA systeme ajoutes a $CERTIFI_PEM"
  else
    echo "   Aucun CA systeme trouve a injecter (probablement hors proxy : OK)."
  fi
else
  echo "   Bundle certifi introuvable ou non modifiable (probablement hors proxy : OK)."
fi

echo "== Setup termine =="
