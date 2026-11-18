"""Penghubung antara aplikasi dan restorasi.py: graf, titik, model, dan menjalankan algoritma."""
import hashlib
import random

import geopandas as gpd
import numpy as np
import pandas as pd
import streamlit as st
from shapely.geometry import Point

import restorasi as rt

NAMA_ALGO = {"greedy": "Greedy", "sa": "Simulated annealing", "optimal": "Optimal (DP)",
             "krus": "Urut krusialitas", "acak": "Acak"}


def md5(*a):
    return hashlib.md5(repr(a).encode()).hexdigest()[:10]


@st.cache_resource(show_spinner="Membangun jaringan jalan...")
def graf(_g, kelas, kec, sig, desimal=5):
    return rt.bangun_graf(_g, list(kelas), dict(kec), desimal)


# ---------------------------------------------------------------- spasial
def utm_dari(gdf):
    return gdf.to_crs(gdf.estimate_utm_crs())


def kena(utm, geoms, buf=0.0):
    """Indeks ruas yang beririsan dengan geometri (EPSG:4326). Titik/garis diberi buffer minimal 20 m."""
    gs = gpd.GeoSeries(list(geoms), crs=4326).to_crs(utm.crs)
    hit = set()
    for g in gs:
        if g is None or g.is_empty:
            continue
        if g.geom_type not in ("Polygon", "MultiPolygon") or buf > 0:
            g = g.buffer(max(buf, 20.0))
        hit |= {int(i) for i in utm.index[utm.geometry.intersects(g)]}
    return sorted(hit)


def terdekat(utm, lat, lng, zoom=15):
    """Ruas terdekat dari klik; toleransi ~14 piksel layar pada zoom saat ini."""
    p = gpd.GeoSeries([Point(lng, lat)], crs=4326).to_crs(utm.crs).iloc[0]
    d = utm.geometry.distance(p)
    mpp = 156543.03 * np.cos(np.radians(lat)) / (2 ** (zoom or 15))
    tol = float(np.clip(mpp * 14, 15, 400))
    return int(d.idxmin()) if d.min() <= tol else None


# ---------------------------------------------------------------- titik
def olah_titik(G, titik):
    t = titik.copy() if titik is not None and len(titik) else pd.DataFrame(columns=["nama", "jenis", "lat", "lon", "bobot"])
    tp = rt.pasang_titik(G, t)
    def nod(j):
        return set(tp.loc[tp.jenis == j, "node"])
    perm = tp[tp.jenis == "permukiman"]
    pop = {}
    for nd, bw in zip(perm["node"], perm["bobot"]):
        pop[nd] = pop.get(nd, 0.0) + float(bw)
    rs = tp[tp.jenis == "rs"]
    return {"tp": tp, "rs": nod("rs"), "posko": nod("posko"), "depot": nod("depot") | nod("quarry"), "pop": pop,
            "perm": [(r["nama"], r["node"], r["bobot"]) for _, r in perm.iterrows()],
            "rs_nama": dict(zip(rs["node"], rs["nama"]))}


def buat_od(G, ti, od_tabel, maks=15):
    """Perjalanan: dari tabel unggahan bila ada, kalau tidak permukiman -> RS terdekat."""
    if od_tabel is not None and len(od_tabel):
        return rt.od_dari_tabel(G, od_tabel.head(maks))
    if ti["rs"] and ti["perm"]:
        od = rt.od_otomatis(G, ti["perm"], list(ti["rs"]), ti["rs_nama"], maks)
        return od, [np.nan] * len(od)
    return [], []


# ---------------------------------------------------------------- hitung
def jalankan(G, P, L, bencana, ti, od, skor, algos, kru, tetap, perkm, it, dt):
    ada = {d["i"] for *_, d in G.edges(data=True)}
    P = {int(i) for i in P} & ada
    L = ({int(i) for i in L} & ada) - P
    if not P:
        return None
    lo, hi = float(skor.min()), float(skor.max())
    krus = ((skor - lo) / ((hi - lo) or 1.0)).to_dict()
    dur = {i: tetap + perkm * G.graph["panjang"][i] / 1000 for i in P}
    M = rt.Model(G, P, {i: rt.HAMBATAN[bencana] for i in L}, rs=ti["rs"], posko=ti["posko"], depot=ti["depot"],
                 pop=ti["pop"], od=od, bobot=rt.PROFIL[bencana], perlu_depot=rt.PERLU_DEPOT[bencana],
                 penalti=7200.0, dur=dur, krus=krus)
    urut, jad, tit, rg = {}, {}, {}, {}
    for a in algos:
        if a == "acak":
            x = sorted(P)
            random.Random(7).shuffle(x)
            u = rt.dekode(M, x)
        else:
            u = rt.susun(M, a, int(it), float(dt))
        urut[a] = u
        jad[a], tit[a] = rt.jadwal(M, u, int(kru))
        rg[a] = rt.ringkas(tit[a])
    terbaik = min(rg, key=lambda a: rg[a]["luas"])
    return {"M": M, "urut": urut, "jad": jad, "tit": tit, "rg": rg, "algos": list(algos), "terbaik": terbaik}
