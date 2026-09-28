#!/usr/bin/env python3
"""Which countries is the capital a poor stand-in for, ecologically?

For every sovereign state (Natural Earth admin-0 grouped by SOVEREIGNT, so
overseas territories count with their state), intersect land with RESOLVE
Ecoregions 2017 (Dinerstein et al. 2017, BioScience 67: 534-545; 846
ecoregions, 14 biomes, 8 biogeographic realms) and record:

- share of area per zone (realm + biome) and per ecoregion
- the capital's ecoregion/zone and that zone's share of the country
- outlying land: other admin units of the state, and parts of the main
  unit more than SEA_KM from its mainland (e.g. French Guiana, Hawaii)
- extent: largest distance across the mainland part

Flags (any of):
  A capital zone covers < 50% of the country
  B more than one realm (>= 1% of area)
  C outlying land >= 1,000 km2
  D mainland extent > 2,500 km and >= 2 zones
  E >= 15% of the land more than 1,500 m above or below the capital (WorldClim 2.1 elevation)
For each flagged country, candidate preload points: every ecoregion with
>= 7% of the area (and every outlying unit), each with its most populous
Natural Earth place.

Output: data/ecozonas.json, and the table printed to stdout.
"""
import json
import os

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.mask import mask as rmask
from shapely.geometry import GeometryCollection, Point

HERE = os.path.dirname(os.path.abspath(__file__))
GEO = os.path.join(HERE, "data", "geo")
EA = "ESRI:54009"   # Mollweide, equal area (m)
SEA_KM = 300
MIN_ECO = 0.07
MIN_OUT_KM2 = 1000
ALT_DIFF = 1500     # m: land this far above/below the capital counts as another altitude zone
ALT_SHARE = 0.15
DEM = rasterio.open(os.path.join(GEO, "wc2.1_10m_elev.tif"))  # WorldClim 2.1, 10 arc-min


def altitude(geom4326, cap_pt4326):
    """(capital elevation, share of land >ALT_DIFF m from it, share >1500 m, share <500 m)"""
    try:
        arr, tr = rmask(DEM, [geom4326], crop=True, filled=True, nodata=-32768)
    except ValueError:
        return None
    a = arr[0].astype(float)
    rows = np.arange(a.shape[0])
    lat = tr.f + (rows + 0.5) * tr.e
    w = np.repeat(np.cos(np.radians(lat))[:, None], a.shape[1], axis=1)
    ok = a > -32768
    if not ok.any():
        return None
    w, a = w[ok], a[ok]
    ce = next(DEM.sample([(cap_pt4326.x, cap_pt4326.y)]))[0] if cap_pt4326 is not None else None
    if ce is None or ce <= -32768:
        ce = None
    far = float(w[np.abs(a - ce) > ALT_DIFF].sum() / w.sum()) if ce is not None else None
    return {"capital_elev_m": None if ce is None else int(ce), "share_far_from_capital_elev": None if far is None else round(far, 3),
            "share_above_1500m": round(float(w[a > 1500].sum() / w.sum()), 3), "share_below_500m": round(float(w[a < 500].sum() / w.sum()), 3)}

eco = gpd.read_file(f"zip://{GEO}/Ecoregions2017.zip")[["ECO_NAME", "BIOME_NAME", "REALM", "geometry"]]
eco = eco[eco["BIOME_NAME"] != "N/A"].copy()
eco["geometry"] = eco.geometry.simplify(0.02).make_valid()
eco = eco.set_crs(4326, allow_override=True)
eco["zone"] = eco["REALM"] + " · " + eco["BIOME_NAME"]

adm = gpd.read_file(f"zip://{GEO}/ne_10m_admin_0_countries.zip")[["ADMIN", "SOVEREIGNT", "ADM0_A3", "SOV_A3", "geometry"]].to_crs(4326)
adm["geometry"] = adm.geometry.simplify(0.02).make_valid()

pp = gpd.read_file(f"zip://{GEO}/ne_10m_populated_places_simple.zip")[["name", "adm0name", "sov0name", "adm0cap", "pop_max", "geometry"]].to_crs(4326)
pp = gpd.sjoin_nearest(pp, eco[["ECO_NAME", "zone", "REALM", "geometry"]], how="left", max_distance=0.5).drop(columns="index_right")
pp = pp[~pp.index.duplicated()]

inter = gpd.overlay(adm, eco, how="intersection", keep_geom_type=True)
inter["km2"] = inter.to_crs(EA).area / 1e6

out = []
for sov, g in inter.groupby("SOVEREIGNT"):
    total = g["km2"].sum()
    if total < 1000:
        continue
    admins = set(g["ADMIN"])
    main = g.loc[g["ADM0_A3"] == g["SOV_A3"], "ADMIN"]
    main_admin = main.iloc[0] if len(main) else g.groupby("ADMIN")["km2"].sum().idxmax()
    places = pp[pp["adm0name"].isin(admins) | (pp["sov0name"] == sov)]
    caps = places[(places["adm0cap"] == 1) & (places["adm0name"] == main_admin)].sort_values("pop_max", ascending=False)
    if not len(caps):
        caps = places[places["adm0cap"] == 1].sort_values("pop_max", ascending=False)
    cap = caps.iloc[0] if len(caps) else None
    cap_pt = gpd.GeoSeries([cap.geometry], crs=4326).to_crs(EA).iloc[0] if cap is not None else None

    # connected land pieces of the main admin unit: mainland = largest piece,
    # outlying = pieces more than SEA_KM from it (Canary Is., Hawaii, French Guiana...)
    mgeo = adm[adm["ADMIN"] == main_admin].to_crs(EA).explode(index_parts=False)
    mgeo = mgeo[mgeo.area > 0]
    mainland_piece = mgeo.geometry.iloc[mgeo.area.argmax()] if len(mgeo) else None
    far_pieces = [gg for gg in mgeo.geometry if mainland_piece is not None and gg.distance(mainland_piece) / 1000 > SEA_KM]
    zones = g.groupby("zone")["km2"].sum() / total
    realms = g.groupby("REALM")["km2"].sum() / total
    cap_zone = cap["zone"] if cap is not None and isinstance(cap["zone"], str) else None
    cap_share = float(zones.get(cap_zone, 0.0)) if cap_zone else None

    extent = 0.0
    if mainland_piece is not None:
        hull = mainland_piece.convex_hull
        if hull.geom_type == "Polygon":
            xy = np.array(hull.exterior.coords)
            d = np.sqrt(((xy[:, None, :] - xy[None, :, :]) ** 2).sum(-1))
            extent = float(d.max() / 1000)

    outlying = []
    for a, ga in g[g["ADMIN"] != main_admin].groupby("ADMIN"):
        km2 = ga["km2"].sum()
        zp = places[places["adm0name"] == a].sort_values("pop_max", ascending=False)
        outlying.append({"name": a, "km2": round(float(km2)), "zone": ga.groupby("zone")["km2"].sum().idxmax(),
                         "city": zp.iloc[0]["name"] if len(zp) else None})
    gm = g[g["ADMIN"] == main_admin].to_crs(EA)
    for piece in far_pieces:
        sub = gm[gm.intersects(piece)].copy()
        if not len(sub):
            continue
        sub["a"] = sub.geometry.buffer(0).intersection(piece.buffer(0)).area / 1e6
        km2 = sub["a"].sum()
        if km2 < 100:
            continue
        p4326 = gpd.GeoSeries([piece], crs=EA).to_crs(4326).iloc[0]
        zp = places[places.within(p4326.buffer(0.05))].sort_values("pop_max", ascending=False)
        outlying.append({"name": f"{main_admin} (ultramar)", "km2": round(float(km2)), "zone": sub.groupby("zone")["a"].sum().idxmax(),
                         "city": zp.iloc[0]["name"] if len(zp) else None})
    # merge far pieces by zone+city
    merged = {}
    for o in outlying:
        k = (o["name"], o["city"])
        if k in merged:
            merged[k]["km2"] += o["km2"]
        else:
            merged[k] = dict(o)
    outlying = sorted(merged.values(), key=lambda o: -o["km2"])
    out_km2 = sum(o["km2"] for o in outlying)

    ecos = g[g["ADMIN"] == main_admin].groupby(["ECO_NAME", "zone"])["km2"].sum().sort_values(ascending=False) / total
    keep = {k for k, v in ecos.items() if v >= MIN_ECO}
    for zn in zones[zones >= 0.05].index:
        zz = ecos[[k for k in ecos.index if k[1] == zn]]
        if len(zz): keep.add(zz.index[0])
    points = []
    for (en, zn), share in ecos.items():
        if (en, zn) not in keep:
            continue
        zp = places[(places["ECO_NAME"] == en) & (places["adm0name"] == main_admin)].sort_values("pop_max", ascending=False)
        points.append({"ecoregion": en, "zone": zn, "share": round(float(share), 3),
                       "city": zp.iloc[0]["name"] if len(zp) else None,
                       "capital_here": cap is not None and cap["ECO_NAME"] == en})

    main_geom = adm[adm["ADMIN"] == main_admin].geometry.union_all() if (adm["ADMIN"] == main_admin).any() else None
    alt = altitude(main_geom, cap.geometry if cap is not None else None) if main_geom is not None else None
    flags = []
    if cap_share is not None and cap_share < 0.5: flags.append("A")
    if (realms >= 0.01).sum() > 1: flags.append("B")
    if out_km2 >= MIN_OUT_KM2: flags.append("C")
    if extent > 2500 and (zones >= 0.05).sum() >= 2: flags.append("D")
    if alt and alt["share_far_from_capital_elev"] is not None and alt["share_far_from_capital_elev"] >= ALT_SHARE: flags.append("E")
    out.append({"country": sov, "km2": round(float(total)), "capital": cap["name"] if cap is not None else None,
                "capital_ecoregion": cap["ECO_NAME"] if cap is not None else None, "capital_zone": cap_zone,
                "capital_zone_share": round(cap_share, 3) if cap_share is not None else None,
                "realms": {r: round(float(s), 3) for r, s in realms.items() if s >= 0.01},
                "extent_km": round(extent), "outlying": [o for o in outlying if o["km2"] >= 100],
                "zones": {z: round(float(s), 3) for z, s in zones.sort_values(ascending=False).items() if s >= 0.05},
                "points": points, "flags": flags,
                "altitude": alt,
                "km2_outside_capital_zone": round(float(total * (1 - (cap_share or 0))))})

os.makedirs(os.path.join(HERE, "data"), exist_ok=True)
json.dump(out, open(os.path.join(HERE, "data", "ecozonas.json"), "w"), indent=1, ensure_ascii=False)
fl = [o for o in out if o["flags"]]
print(len(out), "states,", len(fl), "flagged")
for o in sorted(fl, key=lambda o: -o["km2_outside_capital_zone"]):
    print(f"{o['country']:28} {''.join(o['flags']):5} fuera={o['km2_outside_capital_zone']:>9} cap={o['capital_zone_share']} ext={o['extent_km']} out={sum(x['km2'] for x in o['outlying'])} pts={len(o['points'])}")
