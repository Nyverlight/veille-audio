#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
update_feed.py
Met a jour feed.xml (flux RSS podcast valide) en ajoutant l'episode du jour,
puis applique une retention (garde les N derniers episodes) et supprime les
MP3 qui ne sont plus references.

Usage:
    python scripts/update_feed.py --config config/config.json \
        --audio audio/ia-2026-06-05.mp3 \
        --title "Veille IA - 5 juin 2026" \
        --desc "Resume des actualites IA du jour."
"""

import argparse
import datetime as dt
import html
import json
import re
import sys
from email.utils import format_datetime
from pathlib import Path
from xml.etree import ElementTree as ET

ITUNES = "http://www.itunes.com/dtds/podcast-1.0.dtd"
ET.register_namespace("itunes", ITUNES)


def rfc822(d: dt.datetime) -> str:
    return format_datetime(d)


def parse_existing(feed_path: Path):
    """Retourne la liste des episodes existants [{title,desc,guid,url,length,pubdate}]."""
    episodes = []
    if not feed_path.exists():
        return episodes
    try:
        tree = ET.parse(feed_path)
    except Exception:
        return episodes
    root = tree.getroot()
    channel = root.find("channel")
    if channel is None:
        return episodes
    for item in channel.findall("item"):
        enc = item.find("enclosure")
        episodes.append({
            "title": (item.findtext("title") or "").strip(),
            "desc": (item.findtext("description") or "").strip(),
            "guid": (item.findtext("guid") or "").strip(),
            "url": enc.get("url") if enc is not None else "",
            "length": enc.get("length") if enc is not None else "0",
            "pubdate": (item.findtext("pubDate") or "").strip(),
        })
    return episodes


def build_feed(cfg, episodes):
    base = cfg["base_url"].rstrip("/")
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = cfg["feed_title"]
    ET.SubElement(channel, "link").text = base
    ET.SubElement(channel, "language").text = cfg.get("feed_language", "fr-FR")
    ET.SubElement(channel, "description").text = cfg["feed_description"]
    ET.SubElement(channel, "{%s}author" % ITUNES).text = cfg.get("feed_author", "")
    img = ET.SubElement(channel, "{%s}image" % ITUNES)
    img.set("href", base + "/cover.jpg")
    for ep in episodes:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = ep["title"]
        ET.SubElement(item, "description").text = ep["desc"]
        ET.SubElement(item, "guid").text = ep["guid"]
        ET.SubElement(item, "pubDate").text = ep["pubdate"]
        ET.SubElement(item, "enclosure", {
            "url": ep["url"],
            "length": str(ep["length"]),
            "type": "audio/mpeg",
        })
    return ET.ElementTree(rss)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/config.json")
    ap.add_argument("--audio", required=True, help="chemin du MP3 du jour")
    ap.add_argument("--title", required=True)
    ap.add_argument("--desc", required=True)
    ap.add_argument("--feed", default="feed.xml")
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    base = cfg["base_url"].rstrip("/")
    keep = int(cfg.get("keep_episodes", 14))
    feed_path = Path(args.feed)

    audio_path = Path(args.audio)
    if not audio_path.exists():
        print(f"ERREUR: fichier audio introuvable: {audio_path}", file=sys.stderr)
        sys.exit(1)

    now = dt.datetime.now(dt.timezone.utc)
    new_ep = {
        "title": args.title,
        "desc": args.desc,
        "guid": base + "/" + audio_path.as_posix(),
        "url": base + "/" + audio_path.as_posix(),
        "length": str(audio_path.stat().st_size),
        "pubdate": rfc822(now),
    }

    episodes = parse_existing(feed_path)
    # Evite les doublons si on relance le meme jour
    episodes = [e for e in episodes if e["guid"] != new_ep["guid"]]
    episodes.insert(0, new_ep)
    episodes = episodes[:keep]

    tree = build_feed(cfg, episodes)
    ET.indent(tree, space="  ")
    tree.write(feed_path, encoding="utf-8", xml_declaration=True)
    print(f"feed.xml mis a jour: {len(episodes)} episode(s).")

    # Retention: supprime les MP3 non references
    referenced = set()
    for e in episodes:
        m = re.search(r"/(audio/[^/]+\.mp3)$", e["url"])
        if m:
            referenced.add(m.group(1))
    removed = 0
    for mp3 in Path("audio").glob("*.mp3"):
        rel = mp3.as_posix()
        if rel not in referenced:
            mp3.unlink()
            removed += 1
    print(f"Retention: {removed} ancien(s) MP3 supprime(s).")


if __name__ == "__main__":
    main()
