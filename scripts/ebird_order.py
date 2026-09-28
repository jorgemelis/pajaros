#!/usr/bin/env python3
"""ebird_order.py — Reorder each place's species in places.json from most to
least frequently reported on eBird, so the "Nº de aves" setting (first N
species) keeps the birds you are most likely to see, and store each
species' month-by-month profile in the place's "months" map.

Frequency = number of eBird checklists reporting the species inside a box
around the place, 2016 onwards, counted through GBIF's copy of the eBird
Observation Dataset (EOD, one record per species per checklist). eBird's own
bar-chart downloads need a login; GBIF's API is open. Every species in a
place shares the same denominator (checklists in the box), so ordering by
this count is ordering by eBird frequency.

Months: for each month, the species' records divided by all bird records
in the box that month (so busy birding months don't look better just for
having more observers), scaled so the species' best month is 100.

Stdlib only, no pip install needed.

Usage:
  python3 scripts/ebird_order.py            # print counts and rewrite places.json
  python3 scripts/ebird_order.py --dry-run  # print counts only
"""

import json
import os
import re
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
AVES_TAXON_KEY = 212  # GBIF backbone class Aves

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


def counts_by_month(key, box):
    """Total records and records per month (index 0 = January)."""
    lat, lon = box
    r = get_json("/occurrence/search", datasetKey=EOD_DATASET, taxonKey=key,
                 decimalLatitude=lat, decimalLongitude=lon, year=YEARS, limit=0,
                 facet="month", **{"month.facetLimit": 12})
    by_month = [0] * 12
    for c in r["facets"][0]["counts"] if r["facets"] else []:
        by_month[int(c["name"]) - 1] = c["count"]
    return r["count"], by_month


def month_profile(by_month, all_birds):
    rel = [n / t if t else 0 for n, t in zip(by_month, all_birds)]
    peak = max(rel)
    return [round(100 * x / peak) if peak else 0 for x in rel]


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
        _, all_birds = counts_by_month(AVES_TAXON_KEY, box)
        rows, months = [], {}
        for sp in info["species"]:
            if sp not in keys:
                keys[sp] = taxon_key(sp)
            n, by_month = counts_by_month(keys[sp], box)
            rows.append((n, sp))
            months[sp] = month_profile(by_month, all_birds)
            time.sleep(0.1)  # be polite to the API
        rows.sort(key=lambda r: -r[0])  # stable: ties keep their current order
        print(f"== {place}")
        for n, sp in rows:
            print(f"  {n:6d}  {sp}  " + " ".join(f"{v:3d}" for v in months[sp]))
        info["species"] = [sp for _, sp in rows]
        info["months"] = {sp: months[sp] for _, sp in rows}

    if not dry_run:
        text = json.dumps(data, indent=2, ensure_ascii=False)
        # Keep each 12-month profile on one line instead of one number per line.
        text = re.sub(r"\[\s+(\d+(?:,\s+\d+){11})\s+\]",
                      lambda m: "[" + re.sub(r",\s+", ", ", m.group(1)) + "]", text)
        with open(PLACES, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print("places.json reordered")


if __name__ == "__main__":
    main()
