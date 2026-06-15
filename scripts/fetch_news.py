#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_news.py  (version patchee 2026-06, SANS feedparser)
Recupere les entrees RSS des sources, filtre sur une fenetre temporelle,
effectue un dedoublonnage GROSSIER (par lien et par titre normalise) et
ecrit le resultat dans work/raw_news.json.

Le tri editorial fin (fusion semantique, traduction, resume) est fait
ENSUITE par Claude dans la routine, pas ici.

Cette version n'utilise QUE `requests` + la stdlib (xml.etree), SANS feedparser,
pour eviter la dependance fragile sgmllib3k qui ne se compile pas en cloud.

Changements de cette version (cf. recapitulatif des actions) :
  1. Telechargement avec un User-Agent de NAVIGATEUR reel + en-tete
     Accept/Accept-Language -> contourne la plupart des 403 (anti-bot :
     01net, The Verge, Ars Technica...). C'EST LE CORRECTIF PRINCIPAL.
  2. Compteur "retenus/bruts" sur chaque ligne [OK] -> distingue un flux
     reellement vide ou mal parse (0/0) d'un flux dont tous les articles
     sont simplement plus vieux que la fenetre (0/N).
  3. Rapport de sante des flux en fin d'execution.

Dependances : requests
    pip install requests

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

# curl_cffi imite l'empreinte TLS/JA3 d'un vrai navigateur : INDISPENSABLE
# pour les flux derriere une protection anti-bot (Cloudflare/DataDome) qui
# renvoient 403 a requests meme avec un parfait User-Agent (The Verge,
# Ars Technica, 01net, siecledigital...). Si absent, on retombe sur requests.
try:
    from curl_cffi import requests as cffi_requests
    _HAS_CFFI = True
except ImportError:
    _HAS_CFFI = False


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
    """
    Tente de parser une date RSS (RFC 2822) ou Atom (ISO 8601).
    Retourne un datetime UTC ou None.
    """
    if not date_str:
        return None
    date_str = date_str.strip()
    # Essai ISO 8601 (Atom)
    try:
        result = _parse_iso(date_str)
        if result:
            return result
    except Exception:
        pass
    # Essai RFC 2822 (RSS)
    try:
        return parsedate_to_datetime(date_str).astimezone(dt.timezone.utc)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Fetch + parse d'un flux RSS 2.0 ou Atom 1.0 (stdlib uniquement)
# ---------------------------------------------------------------------------

# Namespaces courants
_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "dc":   "http://purl.org/dc/elements/1.1/",
    "content": "http://purl.org/rss/1.0/modules/content/",
}


def _text(el, *paths):
    """Cherche le premier path qui retourne un texte non vide."""
    for path in paths:
        node = el.find(path, _NS)
        if node is not None and node.text:
            return node.text.strip()
    return ""


def parse_rss_feed(xml_bytes: bytes, url: str) -> dict:
    """
    Parse un flux RSS 2.0 ou Atom 1.0 depuis des bytes bruts.
    Retourne {"title": str, "entries": [{"title","link","published","summary"}]}.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise ValueError(f"XML invalide : {e}")

    tag = root.tag  # peut contenir un namespace ex. {http://...}feed

    # --- Atom ---
    if "feed" in tag:
        feed_title = _text(root, "atom:title", "title") or url
        entries = []
        for entry in root.findall("atom:entry", _NS) or root.findall("entry"):
            title = _text(entry, "atom:title", "title")
            if not title:
                continue
            # Lien : href de <link rel="alternate"> ou premier <link>
            link = ""
            for lk in entry.findall("atom:link", _NS) or entry.findall("link"):
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

    # --- RSS 2.0 (root = <rss> ou <rdf:RDF>) ---
    channel = root.find("channel")
    if channel is None:
        # Essai RDF/RSS 1.0
        channel = root
    feed_title = _text(channel, "title") or url
    entries = []
    # items peuvent etre dans channel ou au niveau root (RSS 1.0)
    item_iter = list(channel.findall("item")) or list(root.findall("item"))
    for item in item_iter:
        title = _text(item, "title")
        if not title:
            continue
        link = _text(item, "link", "dc:identifier")
        # <link> en RSS est parfois du texte CDATA sans balise fermante —
        # ET le lit comme tail ; on essaie aussi le tail de <link>
        if not link:
            lk_el = item.find("link")
            if lk_el is not None:
                link = (lk_el.text or "") + (lk_el.tail or "")
                link = link.strip()
        published = _text(item, "pubDate", "dc:date", "published")
        summary = _text(item, "description", "content:encoded", "summary")
        entries.append({
            "title": title,
            "link": link.strip(),
            "published": published,
            "summary": summary,
        })
    return {"title": feed_title, "entries": entries}


# Profils de navigateurs a imiter (empreinte TLS + en-tetes coherents). On en
# essaie plusieurs car un WAF qui bloque Chrome laisse parfois passer Safari
# ou Firefox. NE PAS forcer d'en-tete User-Agent ici : curl_cffi pose lui-meme
# un jeu d'en-tetes coherent avec l'empreinte TLS choisie (un UA "Chrome" sur
# une empreinte "Firefox" est justement ce que les anti-bots detectent).
_IMPERSONATE_PROFILES = ["chrome", "safari170", "firefox133", "edge101"]


def fetch_feed(url: str, timeout: int = 20) -> dict:
    """
    Telecharge le flux en imitant l'empreinte TLS d'un vrai navigateur
    (curl_cffi) -> contourne les 403 anti-bot (Cloudflare/DataDome) que
    requests ne peut pas passer, meme avec un bon User-Agent.
    Repli sur requests si curl_cffi est indisponible ou epuise.
    Leve une exception en cas d'echec definitif.
    """
    last_err = None

    if _HAS_CFFI:
        for profile in _IMPERSONATE_PROFILES:
            try:
                resp = cffi_requests.get(url, impersonate=profile,
                                         timeout=timeout)
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                continue
            if resp.status_code == 200:
                return parse_rss_feed(resp.content, url)
            last_err = f"HTTP {resp.status_code}"
            # 403/429/503 = anti-bot ou limite de debit -> un autre profil
            # peut passer. Tout autre code (404, 410, 500...) ne sera pas
            # resolu par un changement de profil : on arrete la boucle.
            if resp.status_code not in (403, 429, 503):
                break

    # Repli requests (curl_cffi absent, ou flux sans protection qui aurait
    # rejete les profils ci-dessus pour une autre raison).
    try:
        resp = requests.get(url, headers=BROWSER_HEADERS, timeout=timeout)
        resp.raise_for_status()
        return parse_rss_feed(resp.content, url)
    except Exception as e:
        if last_err:
            raise RuntimeError(f"{last_err} (repli requests: {e})")
        raise


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
            feed = fetch_feed(url)
        except Exception as e:
            print(f"  [ECHEC] {url} -> {e}")
            feeds_failed.append((url, str(e)))
            continue

        source_name = feed["title"]
        raw_count = len(feed["entries"])
        kept = 0

        for e in feed["entries"]:
            when = parse_date(e["published"])

            # On garde si recent, ou si date inconnue (on prefere ne pas perdre)
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
