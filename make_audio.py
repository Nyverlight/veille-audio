#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_audio.py
Transforme un fichier texte en MP3 via edge-tts (voix neuronales Microsoft,
gratuites). La voix et le debit sont lus dans config.json.

Note SSL : edge-tts code en dur son contexte SSL avec certifi et ignore
SSL_CERT_FILE / REQUESTS_CA_BUNDLE. Derriere un proxy d'inspection TLS (bac a
sable cloud), le CA du proxy n'est pas reconnu -> on reconstruit le contexte SSL
interne d'edge-tts en y ajoutant les CA systeme. C'est une ceinture-bretelles
avec l'injection faite dans setup.sh ; en local hors proxy, ca ne change rien.

Usage:
    python scripts/make_audio.py --config config/config.json \
        --in work/script.txt --out audio/ia-2026-06-05.mp3
"""

import argparse
import asyncio
import json
import os
import ssl
import sys
from pathlib import Path

try:
    import edge_tts
except ImportError:
    print("ERREUR: le module 'edge-tts' n'est pas installe. "
          "Lancez d'abord setup.sh (ou: pip install edge-tts).",
          file=sys.stderr)
    sys.exit(1)


def harden_ssl_for_edge_tts() -> None:
    """Ajoute les CA systeme (dont le CA du proxy) au contexte SSL d'edge-tts."""
    try:
        import certifi
        import edge_tts.communicate as _ec
        ctx = ssl.create_default_context(cafile=certifi.where())
        for ca in (os.environ.get("SSL_CERT_FILE"),
                   "/etc/ssl/certs/ca-certificates.crt"):
            if ca and os.path.exists(ca):
                try:
                    ctx.load_verify_locations(cafile=ca)
                except ssl.SSLError:
                    pass
        if hasattr(_ec, "_SSL_CTX"):
            _ec._SSL_CTX = ctx
    except Exception:
        # En local (hors proxy d'inspection), le contexte par defaut suffit.
        pass


async def synth(text: str, out_path: str, voice: str, rate: str):
    communicate = edge_tts.Communicate(text, voice=voice, rate=rate)
    await communicate.save(out_path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/config.json")
    ap.add_argument("--in", dest="infile", required=True)
    ap.add_argument("--out", dest="outfile", required=True)
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    voice = cfg.get("voice", "fr-FR-DeniseNeural")
    rate = cfg.get("rate", "+0%")

    text = Path(args.infile).read_text(encoding="utf-8").strip()
    if not text:
        print("ERREUR: le fichier texte est vide.", file=sys.stderr)
        sys.exit(1)

    harden_ssl_for_edge_tts()

    Path(args.outfile).parent.mkdir(parents=True, exist_ok=True)
    print(f"Synthese vocale (voix={voice}, debit={rate}) -> {args.outfile}")
    asyncio.run(synth(text, args.outfile, voice, rate))

    size = Path(args.outfile).stat().st_size
    if size < 1024:
        print(f"ATTENTION: fichier audio suspicieusement petit ({size} octets).",
              file=sys.stderr)
    print(f"OK: {args.outfile} ({size} octets)")


if __name__ == "__main__":
    main()
