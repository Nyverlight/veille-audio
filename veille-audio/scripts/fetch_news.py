#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_news.py
Recupere les entrees RSS des sources, filtre sur une fenetre temporelle
(la veille), effectue un dedoublonnage GROSSIER (par lien et par titre
normalise) et ecrit le resultat dans work/raw_news.json.

Le tri editorial fin (fusion semantique de sujets identiques, traduction,
resume) est fait ENSUITE par Claude dans la routine, pas ici.

Usage:
    python scripts/fetch_news.py --config config/config.json
"""

import argparse
import datetime as dt
import json
import re
import sys
import unicodedata
from pathlib import Path

try:
    import feedparser
except ImportError:
    print("ERREUR: le module 'feedparser' n'est pas installe. "
          "Lancez d'abord setup.sh (ou: pip install feedparser).",
          file=sys.stderr)
    sys.exit(1)


def normalize_title(title: str) -> str:
    """Normalise un titre pour comparaison (minuscules, sans accents/ponctuation)."""
    t = unicodedata.normalize("NFKD", title or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.lower()
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def entry_datetime(entry):
    """Retourne un datetime UTC si dispo, sinon None."""
    for key in ("published_parsed", "updated_parsed"):
        val = entry.get(key)
        if val:
            try:
                return dt.datetime(*val[:6], tzinfo=dt.timezone.utc)
            except Exception:
                pass
    return None


def clean_text(html: str) -> str:
    """Retire les balises HTML d'un resume et compacte les espaces."""
    txt = re.sub(r"<[^>]+>", " ", html or "")
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/config.json")
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    sources_file = Path(cfg["sources_file"])
    lookback_hours = int(cfg.get("lookback_hours", 30))
    max_items = int(cfg.get("max_items_fetch", 60))

    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(hours=lookback_hours)

    # Lecture des sources
    sources = []
    for line in sources_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            sources.append(line)

    seen_links = set()
    seen_titles = set()
    items = []

    print(f"=== Recuperation de {len(sources)} flux "
          f"(fenetre: {lookback_hours}h) ===")

    for url in sources:
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            print(f"  [ECHEC] {url} -> {e}")
            continue

        if getattr(feed, "bozo", 0) and not feed.entries:
            print(f"  [ECHEC] {url} -> flux illisible ou injoignable")
            continue

        source_name = (feed.feed.get("title") or url) if feed.feed else url
        kept = 0
        for e in feed.entries:
            when = entry_datetime(e)
            # On garde si recent, ou si date inconnue (on prefere ne pas perdre)
            if when is not None and when < cutoff:
                continue

            link = (e.get("link") or "").strip()
            title = (e.get("title") or "").strip()
            if not title:
                continue

            norm = normalize_title(title)
            if link and link in seen_links:
                continue
            if norm and norm in seen_titles:
                continue
            seen_links.add(link)
            seen_titles.add(norm)

            items.append({
                "title": title,
                "link": link,
                "source": source_name,
                "published": when.isoformat() if when else None,
                "summary": clean_text(e.get("summary", ""))[:600],
            })
            kept += 1

        print(f"  [OK]    {source_name}: {kept} item(s) retenu(s)")

    # Tri par date (les plus recents d'abord), inconnus a la fin
    items.sort(key=lambda x: x["published"] or "", reverse=True)
    items = items[:max_items]

    out = Path("work/raw_news.json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(items, ensure_ascii=False, indent=2),
                   encoding="utf-8")

    print(f"=== {len(items)} actualite(s) ecrite(s) dans {out} ===")
    if not items:
        print("ATTENTION: aucune actualite recuperee. "
              "Verifiez l'acces reseau aux domaines des flux, "
              "et la validite des URLs dans sources_ia.txt.")


if __name__ == "__main__":
    main()
