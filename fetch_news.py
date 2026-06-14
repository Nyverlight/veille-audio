#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_news.py
Recupere les entrees RSS des sources, filtre sur une fenetre temporelle,
effectue un dedoublonnage GROSSIER (par lien et par titre normalise) et ecrit
le resultat dans work/raw_news.json.

Le tri editorial fin (fusion semantique, traduction, resume) est fait ENSUITE
par Claude dans la routine, pas ici.

Cette version n'utilise que `requests` + la stdlib (xml.etree), sans feedparser,
pour eviter la dependance fragile sgmllib3k qui ne se compile pas en cloud.

Usage:
    python scripts/fetch_news.py --config config/config.json
"""

import argparse
import datetime as dt
import json
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERREUR: le module 'requests' n'est pas installe. "
          "Lancez d'abord setup.sh (ou: pip install requests).",
          file=sys.stderr)
    sys.exit(1)


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


# ---------------------------------------------------------------------------
# Parsing de dates RSS / Atom (stdlib uniquement)
# ---------------------------------------------------------------------------

_ISO_RE = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2}).*?(Z|[+-]\d{2}:?\d{2})?$"
)


def _parse_iso(s: str):
    """Parse une date ISO 8601 basique en datetime UTC."""
    m = _ISO_RE.match(s.strip())
    if not m:
        return None
    parts = [int(x) for x in m.groups()[:6]]
    tz_str = m.group(7)
    if tz_str in (None, "Z", "+00:00", "-00:00"):
        tz = dt.timezone.utc
    else:
        sign = 1 if tz_str[0] == "+" else -1
        tz_str_clean = tz_str[1:].replace(":", "")
        h, mi = int(tz_str_clean[:2]), int(tz_str_clean[2:])
        tz = dt.timezone(dt.timedelta(hours=sign * h, minutes=sign * mi))
    return dt.datetime(*parts, tzinfo=tz).astimezone(dt.timezone.utc)


def parse_date(date_str: str):
    """Tente de parser une date RSS (RFC 2822) ou Atom (ISO 8601). UTC ou None."""
    if not date_str:
        return None
    date_str = date_str.strip()
    try:
        result = _parse_iso(date_str)
        if result:
            return result
    except Exception:
        pass
    try:
        return parsedate_to_datetime(date_str).astimezone(dt.timezone.utc)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Fetch + parse d'un flux RSS 2.0 ou Atom 1.0
# ---------------------------------------------------------------------------

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "dc":   "http://purl.org/dc/elements/1.1/",
    "content": "http://purl.org/rss/1.0/modules/content/",
}


def _text(el, *paths):
    for path in paths:
        node = el.find(path, _NS)
        if node is not None and node.text:
            return node.text.strip()
    return ""


def parse_rss_feed(xml_bytes: bytes, url: str) -> dict:
    """Parse un flux RSS 2.0 ou Atom 1.0. Retourne {title, entries:[...]}."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise ValueError(f"XML invalide : {e}")

    tag = root.tag

    # --- Atom ---
    if "feed" in tag:
        feed_title = _text(root, "atom:title", "title") or url
        entries = []
        atom_entries = root.findall("atom:entry", _NS)
        if not atom_entries:
            atom_entries = root.findall("entry")
        for entry in atom_entries:
            title = _text(entry, "atom:title", "title")
            if not title:
                continue
            link = ""
            links = entry.findall("atom:link", _NS)
            if not links:
                links = entry.findall("link")
            for lk in links:
                rel = lk.get("rel", "alternate")
                if rel in ("alternate", ""):
                    link = lk.get("href", "")
                    break
            published = _text(entry, "atom:published", "atom:updated",
                              "published", "updated")
            summary = _text(entry, "atom:summary", "atom:content",
                            "summary", "content")
            entries.append({
                "title": title,
                "link": link.strip(),
                "published": published,
                "summary": summary,
            })
        return {"title": feed_title, "entries": entries}

    # --- RSS 2.0 / RDF ---
    channel = root.find("channel")
    if channel is None:
        channel = root
    feed_title = _text(channel, "title") or url
    entries = []
    item_iter = list(channel.findall("item"))
    if not item_iter:
        item_iter = list(root.findall("item"))
    for item in item_iter:
        title = _text(item, "title")
        if not title:
            continue
        link = _text(item, "link", "dc:identifier")
        if not link:
            lk_el = item.find("link")
            if lk_el is not None:
                link = ((lk_el.text or "") + (lk_el.tail or "")).strip()
        published = _text(item, "pubDate", "dc:date", "published")
        summary = _text(item, "description", "content:encoded", "summary")
        entries.append({
            "title": title,
            "link": link.strip(),
            "published": published,
            "summary": summary,
        })
    return {"title": feed_title, "entries": entries}


def fetch_feed(url: str, timeout: int = 15) -> dict:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; fetch_news/3.0; +https://github.com/)"
        )
    }
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return parse_rss_feed(resp.content, url)


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

    sources = []
    for line in sources_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            sources.append(line)

    seen_links: set = set()
    seen_titles: set = set()
    items = []
    ok_feeds, empty_feeds, failed_feeds = [], [], []

    print(f"=== Recuperation de {len(sources)} flux "
          f"(fenetre: {lookback_hours}h) ===")

    for url in sources:
        try:
            feed = fetch_feed(url)
        except Exception as e:
            print(f"  [ECHEC] {url} -> {e}")
            failed_feeds.append(url)
            continue

        source_name = feed["title"]
        kept = 0
        for e in feed["entries"]:
            when = parse_date(e["published"])
            if when is not None and when < cutoff:
                continue
            link = e["link"]
            title = e["title"]
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
                "summary": clean_text(e["summary"])[:600],
            })
            kept += 1

        print(f"  [OK]    {source_name}: {kept} item(s) retenu(s)")
        (ok_feeds if kept else empty_feeds).append(source_name)

    items.sort(key=lambda x: x["published"] or "", reverse=True)
    items = items[:max_items]

    out = Path("work/raw_news.json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(items, ensure_ascii=False, indent=2),
                   encoding="utf-8")

    # --- Rapport de sante des flux (facilite le diagnostic) ---
    print("\n=== Sante des flux ===")
    print(f"  Avec items : {len(ok_feeds)}")
    print(f"  Joignables mais vides : {len(empty_feeds)}")
    print(f"  En echec (reseau/URL) : {len(failed_feeds)}")
    if failed_feeds:
        print("  -> Flux en echec a verifier (allow-list reseau ou URL morte) :")
        for u in failed_feeds:
            print(f"       {u}")

    print(f"\n=== {len(items)} actualite(s) ecrite(s) dans {out} ===")
    if not items:
        print("ATTENTION: aucune actualite recuperee. "
              "Verifiez l'acces reseau aux domaines des flux, "
              "et la validite des URLs dans le fichier de sources.")


if __name__ == "__main__":
    main()
