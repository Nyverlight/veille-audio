#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_news.py  (version patchee 2026-06)
Recupere les entrees RSS des sources, filtre sur une fenetre temporelle,
effectue un dedoublonnage GROSSIER (par lien et par titre normalise) et
ecrit le resultat dans work/raw_news.json.

Le tri editorial fin (fusion semantique, traduction, resume) est fait
ENSUITE par Claude dans la routine, pas ici.

Changements de cette version (cf. recapitulatif des actions) :
  1. Telechargement via requests avec un User-Agent de NAVIGATEUR reel
     + en-tete Accept/Accept-Language -> contourne la plupart des 403
     (anti-bot : 01net, The Verge, Ars Technica...).
  2. Parsing delegue a feedparser (robuste sur tous les formats RSS/Atom)
     -> supprime une partie des flux "joignables mais vides" causes par un
     parseur maison trop fragile.
  3. Compteur "retenus/bruts" sur chaque ligne [OK] -> permet de distinguer
     un flux reellement vide (0/0) d'un flux dont tous les articles sont
     simplement plus vieux que la fenetre (0/N).

Dependances :
    pip install requests feedparser

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
    import requests
except ImportError:
    print("ERREUR: le module 'requests' n'est pas installe. "
          "Lancez : pip install requests",
          file=sys.stderr)
    sys.exit(1)

try:
    import feedparser
except ImportError:
    print("ERREUR: le module 'feedparser' n'est pas installe. "
          "Lancez : pip install feedparser",
          file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# En-tetes "navigateur" : indispensable pour ne pas etre rejete (403) par les
# sites a protection anti-bot. Un User-Agent type "fetch_news/2.0" est bloque
# par defaut sur The Verge (Vox Media), Ars Technica, 01net, etc.
# ---------------------------------------------------------------------------

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "application/rss+xml, application/atom+xml, "
        "application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}


# ---------------------------------------------------------------------------
# Helpers de normalisation / nettoyage
# ---------------------------------------------------------------------------

def normalize_title(title: str) -> str:
    """Normalise un titre pour comparaison (minuscules, sans accents/ponctuation)."""
    t = unicodedata.normalize("NFKD", title or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.lower()
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def clean_text(html: str) -> str:
    """Retire les balises HTML d'un resume et compacte les espaces."""
    txt = re.sub(r"<[^>]+>", " ", html or "")
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt


def entry_datetime(entry):
    """Retourne un datetime UTC depuis les champs parses par feedparser, sinon None."""
    for key in ("published_parsed", "updated_parsed"):
        val = entry.get(key)
        if val:
            try:
                return dt.datetime(*val[:6], tzinfo=dt.timezone.utc)
            except Exception:
                pass
    return None


# ---------------------------------------------------------------------------
# Fetch (requests, en-tetes navigateur) + parse (feedparser)
# ---------------------------------------------------------------------------

def fetch_feed(url: str, timeout: int = 15):
    """
    Telecharge le flux avec un User-Agent navigateur (contre les 403) puis le
    parse avec feedparser. Leve une exception en cas d'echec HTTP/reseau.
    Retourne (feed_title, entries) ou entries est la liste feedparser.
    """
    resp = requests.get(url, headers=BROWSER_HEADERS, timeout=timeout)
    resp.raise_for_status()
    fp = feedparser.parse(resp.content)
    feed_title = (fp.feed.get("title") if getattr(fp, "feed", None) else None) or url
    return feed_title, fp.entries


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

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

    seen_links: set = set()
    seen_titles: set = set()
    items = []

    # Compteurs pour le rapport de sante
    feeds_with_items = 0
    feeds_reachable_empty = 0
    feeds_failed = []  # (url, raison)

    print(f"=== Recuperation de {len(sources)} flux "
          f"(fenetre: {lookback_hours}h) ===")

    for url in sources:
        try:
            source_name, entries = fetch_feed(url)
        except Exception as e:
            print(f"  [ECHEC] {url} -> {e}")
            feeds_failed.append((url, str(e)))
            continue

        raw_count = len(entries)
        kept = 0

        for e in entries:
            title = (e.get("title") or "").strip()
            if not title:
                continue

            when = entry_datetime(e)
            # On garde si recent, ou si date inconnue (on prefere ne pas perdre)
            if when is not None and when < cutoff:
                continue

            link = (e.get("link") or "").strip()
            norm = normalize_title(title)
            if link and link in seen_links:
                continue
            if norm and norm in seen_titles:
                continue
            seen_links.add(link)
            seen_titles.add(norm)

            summary = e.get("summary") or e.get("description") or ""
            items.append({
                "title": title,
                "link": link,
                "source": source_name,
                "published": when.isoformat() if when else None,
                "summary": clean_text(summary)[:600],
            })
            kept += 1

        # Le compteur kept/raw distingue :
        #   0/0  -> flux reellement vide ou mal parse (a verifier)
        #   0/N  -> N articles existent mais tous plus vieux que la fenetre
        print(f"  [OK]    {source_name}: {kept}/{raw_count} item(s) retenu(s)")
        if kept > 0:
            feeds_with_items += 1
        else:
            feeds_reachable_empty += 1

    # Tri par date (les plus recents d'abord), inconnus a la fin
    items.sort(key=lambda x: x["published"] or "", reverse=True)
    items = items[:max_items]

    out = Path("work/raw_news.json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(items, ensure_ascii=False, indent=2),
                   encoding="utf-8")

    # --- Rapport de sante des flux ---
    print("=== Sante des flux ===")
    print(f"  Avec items            : {feeds_with_items}")
    print(f"  Joignables mais vides : {feeds_reachable_empty}")
    print(f"  En echec (reseau/URL) : {len(feeds_failed)}")
    if feeds_failed:
        print("  -> Flux en echec a verifier (allow-list reseau, anti-bot "
              "ou URL morte) :")
        for url, reason in feeds_failed:
            print(f"       {url}")

    print(f"=== {len(items)} actualite(s) ecrite(s) dans {out} ===")
    if not items:
        print("ATTENTION: aucune actualite recuperee. "
              "Verifiez l'acces reseau aux domaines des flux, "
              "et la validite des URLs dans le fichier de sources.")


if __name__ == "__main__":
    main()
