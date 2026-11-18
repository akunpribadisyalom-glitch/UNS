"""Cari nama jalan tiap ruas dari OpenStreetMap (Overpass API), lalu cocokkan secara spasial.

Tanpa ketergantungan Streamlit. Butuh internet. Hasil unduhan disimpan di folder sementara
supaya data yang sama tidak diunduh dua kali.

Cara kerja:
1. Ambil semua jalan ber-nama di sekitar ruas (dipecah per kotak 0,1 derajat, hanya kotak yang berisi ruas).
2. Untuk tiap ruas, ambil beberapa titik sampel di sepanjang garisnya.
3. Tiap titik sampel dicocokkan ke jalan OSM terdekat (maks. `TOLERANSI_M` meter).
4. Nama yang paling banyak dipilih (suara tertimbang jarak) menjadi nama jalan ruas itu.
"""
import hashlib
import json
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict

import numpy as np
import pandas as pd
from shapely.geometry import LineString
from shapely.strtree import STRtree

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
JENIS_JALAN = ("motorway|trunk|primary|secondary|tertiary|unclassified|residential|living_street|service|road|"
               "motorway_link|trunk_link|primary_link|secondary_link|tertiary_link")
UKURAN_KOTAK = 0.1        # derajat
MAKS_KOTAK = 60
TOLERANSI_M = 25.0
CACHE = os.path.join(tempfile.gettempdir(), "jalan_siaga_osm")
UA = "JalanSiaga/1.0 (pencarian nama jalan untuk analisis pemulihan jalan pascabanjir)"


class GagalOverpass(Exception):
    pass


# ---------------------------------------------------------------- unduh
def _query(s, w, n, e):
    return (f'[out:json][timeout:90];way["highway"~"^({JENIS_JALAN})$"]["name"]({s:.6f},{w:.6f},{n:.6f},{e:.6f});'
            "out tags geom;")


def ambil_overpass(s, w, n, e, tunggu=1.0):
    """Unduh jalan ber-nama dalam satu kotak. Mengembalikan list dict {nama, ref, koor:[(lon,lat),...]}."""
    kunci = hashlib.md5(f"{s:.5f}{w:.5f}{n:.5f}{e:.5f}".encode()).hexdigest()
    os.makedirs(CACHE, exist_ok=True)
    f = os.path.join(CACHE, kunci + ".json")
    if os.path.exists(f):
        with open(f, encoding="utf-8") as fh:
            return json.load(fh)
    data = urllib.parse.urlencode({"data": _query(s, w, n, e)}).encode()
    galat = []
    for ep in ENDPOINTS:
        for coba in range(2):
            try:
                req = urllib.request.Request(ep, data=data, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=100) as r:
                    j = json.loads(r.read().decode("utf-8"))
                hasil = []
                for el in j.get("elements", []):
                    if el.get("type") != "way" or not el.get("geometry"):
                        continue
                    tg = el.get("tags", {})
                    nm = tg.get("name") or tg.get("ref")
                    if not nm:
                        continue
                    hasil.append({"nama": nm, "koor": [(p["lon"], p["lat"]) for p in el["geometry"]]})
                with open(f, "w", encoding="utf-8") as fh:
                    json.dump(hasil, fh)
                time.sleep(tunggu)
                return hasil
            except (urllib.error.URLError, TimeoutError, ValueError, OSError) as ex:
                galat.append(f"{ep}: {ex}")
                time.sleep(1.5 * (coba + 1))
    raise GagalOverpass("; ".join(galat[-3:]))


# ---------------------------------------------------------------- kotak
def kotak_perlu(gdf, ukuran=UKURAN_KOTAK, margin=0.002):
    """Daftar kotak (s, w, n, e) yang berisi ruas."""
    b = gdf.geometry.bounds.to_numpy()
    kunci = set()
    for x0, y0, x1, y1 in b:
        for i in range(int(np.floor((x0 - margin) / ukuran)), int(np.floor((x1 + margin) / ukuran)) + 1):
            for j in range(int(np.floor((y0 - margin) / ukuran)), int(np.floor((y1 + margin) / ukuran)) + 1):
                kunci.add((i, j))
    return [(j * ukuran - margin, i * ukuran - margin, (j + 1) * ukuran + margin, (i + 1) * ukuran + margin)
            for i, j in sorted(kunci)]


# ---------------------------------------------------------------- cocokkan
def _sampel(geom, jarak=12.0, maks=15):
    """Titik sampel merata sepanjang garis (di CRS meter)."""
    bagian = list(geom.geoms) if geom.geom_type == "MultiLineString" else [geom]
    pts = []
    for b in bagian:
        if b.length <= 0:
            continue
        k = int(np.clip(round(b.length / jarak), 1, maks))
        pts += [b.interpolate((t + .5) / k, normalized=True) for t in range(k)]
    return pts or [geom.representative_point()]


def cocokkan(utm, osm, toleransi=TOLERANSI_M):
    """utm: GeoDataFrame ruas (CRS meter). osm: list dict hasil ambil_overpass (koordinat lon/lat).

    Mengembalikan DataFrame (index sama dengan utm) kolom nama_jalan dan yakin_pct.
    """
    import geopandas as gpd

    out = pd.DataFrame({"nama_jalan": pd.Series("", index=utm.index, dtype=object),
                        "yakin_pct": pd.Series(0.0, index=utm.index)})
    uniq = {}
    for w in osm:
        if len(w["koor"]) >= 2:
            uniq[(w["nama"], tuple(w["koor"][:2]), len(w["koor"]))] = w
    if not uniq:
        return out
    ws = list(uniq.values())
    gl = gpd.GeoSeries([LineString(w["koor"]) for w in ws], crs=4326).to_crs(utm.crs)
    nama = [w["nama"] for w in ws]
    tree = STRtree(list(gl.values))
    for idx, geom in zip(utm.index, utm.geometry):
        if geom is None or geom.is_empty:
            continue
        pts = _sampel(geom)
        hit = tree.query_nearest(pts, max_distance=toleransi, return_distance=True, all_matches=False)
        a, d = hit
        suara = defaultdict(float)
        for t_i, dist in zip(a[1], d):
            suara[nama[int(t_i)]] += 1.0 / (1.0 + float(dist))
        if not suara:
            continue
        top = max(suara, key=suara.get)
        out.at[idx, "nama_jalan"] = top
        out.at[idx, "yakin_pct"] = round(100 * min(suara[top] / (sum(suara.values()) or 1.0), 1.0), 0)
    return out


def cari_nama(gdf, utm, progres=None, ambil=ambil_overpass):
    """Alur lengkap. gdf EPSG:4326, utm = gdf dalam CRS meter. progres(i, n) dipanggil per kotak.

    Mengembalikan (DataFrame nama_jalan/yakin_pct, catatan teks). Memunculkan GagalOverpass bila SEMUA kotak gagal.
    """
    kotak = kotak_perlu(gdf)
    if len(kotak) > MAKS_KOTAK:
        raise GagalOverpass(f"Area terlalu luas ({len(kotak)} kotak, batas {MAKS_KOTAK}). "
                            "Isi kolom nama jalan di file jalan, atau potong area yang diunggah.")
    osm, gagal = [], 0
    for k, kt in enumerate(kotak):
        if progres:
            progres(k, len(kotak))
        try:
            osm += ambil(*kt)
        except GagalOverpass:
            gagal += 1
    if gagal == len(kotak):
        raise GagalOverpass("Semua server Overpass tidak bisa dihubungi. Periksa koneksi internet lalu coba lagi.")
    hasil = cocokkan(utm, osm)
    ada = int((hasil["nama_jalan"] != "").sum())
    cat = f"{ada} dari {len(hasil)} ruas mendapat nama jalan dari OpenStreetMap."
    if gagal:
        cat += f" {gagal} dari {len(kotak)} kotak peta gagal diunduh, jadi sebagian ruas mungkin belum bernama."
    return hasil, cat
