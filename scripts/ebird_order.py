#!/usr/bin/env python3
"""ebird_order.py — Reorder each place's species in places.json from most to
least frequently reported on eBird, so the "Nº de aves" setting (first N
species) keeps the birds you are most likely to see.

Frequency = number of eBird checklists reporting the species inside a box
around the place, 2016 onwards, counted through GBIF's copy of the eBird
Observation Dataset (EOD, one record per species per checklist). eBird's own
bar-chart downloads need a login; GBIF's API is open. Every species in a
place shares the same denominator (checklists in the box), so ordering by
this count is ordering by eBird frequency.

Stdlib only, no pip install needed.

Usage:
  python3 scripts/ebird_order.py            # print counts and rewrite places.json
  python3 scripts/ebird_order.py --dry-run  # print counts only
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.join(os.path.dirname(__file__), "..")
PLACES = os.path.join(ROOT, "places.json")
UA = "PajarosFamilyGuide/1.0 (personal non-commercial family project)"
GBIF = "https://api.gbif.org/v1"
EOD_DATASET = "4fa7b334-ce0d-4e88-aaae-2e0c138d049e"
YEARS = "2016,2026"

# Box around each place: (lat min,max), (lon min,max). A new place needs one.
BOXES = {
    "alicante": ("38.28,38.42", "-0.60,-0.40"),
    "ourense":  ("42.28,42.40", "-7.95,-7.80"),
    "bruselas": ("50.76,50.92", "4.24,4.48"),
    "pozuelo":  ("40.30,40.42", "-3.40,-3.22"),
    "viveiro":  ("43.60,43.72", "-7.68,-7.52"),
}


def get_json(path, **query):
    url = f"{GBIF}{path}?{urllib.parse.urlencode(query)}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def taxon_key(scientific_name):
    m = get_json("/species/match", name=scientific_name, kingdom="Animalia")
    if m.get("class") != "Aves" or not m.get("usageKey"):
        raise ValueError(f"no GBIF bird taxon for {scientific_name}")
    return m["usageKey"]


def checklist_count(key, box):
    lat, lon = box
    return get_json("/occurrence/search", datasetKey=EOD_DATASET, taxonKey=key,
                    decimalLatitude=lat, decimalLongitude=lon, year=YEARS, limit=0)["count"]


def main():
    dry_run = "--dry-run" in sys.argv
    with open(PLACES, encoding="utf-8") as f:
        data = json.load(f)

    keys = {}
    for place, info in data["places"].items():
        box = BOXES.get(place)
        if not box:
            print(f"== {place}: no box in BOXES, left as is")
            continue
        rows = []
        for sp in info["species"]:
            if sp not in keys:
                keys[sp] = taxon_key(sp)
            rows.append((checklist_count(keys[sp], box), sp))
            time.sleep(0.1)  # be polite to the API
        rows.sort(key=lambda r: -r[0])  # stable: ties keep their current order
        print(f"== {place}")
        for n, sp in rows:
            print(f"  {n:6d}  {sp}")
        info["species"] = [sp for _, sp in rows]

    if not dry_run:
        with open(PLACES, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print("places.json reordered")


if __name__ == "__main__":
    main()
