"""Baca file unggahan dan tebak perannya (jalan, titik, area gangguan, perjalanan, atribut)."""
import io
import os
import re
import tempfile
import zipfile

import geopandas as gpd
import numpy as np
import pandas as pd
import streamlit as st
from shapely.geometry import LineString

TITIK_COLS = ["nama", "jenis", "lat", "lon", "bobot"]
PERAN = ["otomatis", "jalan", "titik", "gangguan", "perjalanan", "atribut", "abaikan"]

K_TRIP = r"perjalanan|trip|rute|(^|[^a-z])od([^a-z]|$)"
K_GANGGUAN = r"banjir|genang|longsor|gangguan|putus|rusak|terdampak|bencana|flood|landslide"
K_PERM = r"permukiman|pemukiman|desa|kampung|penduduk|kawasan|dusun|village"
POLA_JENIS = [
    ("rs", r"(^|[^a-z])rs([^a-z]|$)|rsud|rumah ?sakit|hospital|puskesmas|klinik|faskes|health"),
    ("quarry", r"quarry|galian|tambang|borrow|material|sirtu|batu|pasir"),
    ("depot", r"alat ?berat|depot|bpbd|dinas|(^|[^a-z])pu([^a-z]|$)|dpupr|excavator|beko|loader|garasi|workshop"),
    ("posko", r"posko|pengungsi|evakuasi|shelter|assembly|command"),
    ("permukiman", r"permukiman|pemukiman|desa|kampung|rumah|penduduk|residen|dusun|village"),
]


@st.cache_data(show_spinner="Membaca data...")
def baca_geo(nama, data):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, os.path.basename(nama))
        with open(p, "wb") as fh:
            fh.write(data)
        if nama.lower().endswith(".zip"):
            zipfile.ZipFile(p).extractall(d)
            p = next(os.path.join(r, f) for r, _, fs in os.walk(d) for f in fs if f.lower().endswith(".shp"))
        return gpd.read_file(p)


@st.cache_data(show_spinner="Membaca tabel...")
def baca_tabel(nama, data):
    b = io.BytesIO(data)
    if nama.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(b)
    return pd.read_csv(b, sep=None, engine="python")


def norm_jenis(x):
    x = str(x).lower()
    for k, p in POLA_JENIS:
        if re.search(p, x):
            return k
    return None


def _kolom(df, pola):
    return next((c for c in df.columns if re.search(pola, str(c).lower())), None)


def _latlon(t):
    lat = next((c for c in t.columns if re.fullmatch(r"lat|latitude|y", str(c).lower().strip())), None)
    lon = next((c for c in t.columns if re.fullmatch(r"lon|lng|long|longitude|x", str(c).lower().strip())), None)
    return (lat, lon) if lat is not None and lon is not None else None


def tebak_peran(nama, obj):
    n = nama.lower()
    if isinstance(obj, pd.DataFrame) and not isinstance(obj, gpd.GeoDataFrame):
        c = {str(x).lower().strip() for x in obj.columns}
        if {"asal_lat", "asal_lon", "tujuan_lat", "tujuan_lon"} <= c:
            return "perjalanan"
        return "titik" if _latlon(obj) else "atribut"
    tipe = set(obj.geom_type.dropna())
    if all("Line" in t for t in tipe):
        if re.search(K_TRIP, n):
            return "perjalanan"
        return "gangguan" if re.search(K_GANGGUAN, n) else "jalan"
    if all("Point" in t for t in tipe):
        return "titik"
    return "titik" if re.search(K_PERM, n) else "gangguan"


def baca_berkas(f, epsg=32749):
    """Return (peran_tebakan, objek). Objek GeoDataFrame (EPSG:4326) atau DataFrame."""
    nm = f.name.lower()
    if nm.endswith((".csv", ".xlsx", ".xls")):
        t = baca_tabel(f.name, f.getvalue())
        return tebak_peran(f.name, t), t
    d = baca_geo(f.name, f.getvalue())
    if d.crs is None:
        b = d.total_bounds
        d = d.set_crs(4326 if (abs(b) <= 180).all() else int(epsg))
    d = d.to_crs(4326)
    d = d[d.geometry.notna() & ~d.geometry.is_empty].reset_index(drop=True)
    return tebak_peran(f.name, d), d


def titik_dari(nama_file, obj):
    """GeoDataFrame atau tabel lat/lon -> DataFrame titik standar."""
    if isinstance(obj, gpd.GeoDataFrame):
        p = obj.geometry.representative_point()
        lat, lon = p.y.values, p.x.values
        d = obj.drop(columns="geometry")
    else:
        ll = _latlon(obj)
        d = obj.copy()
        lat = pd.to_numeric(d[ll[0]], errors="coerce").values
        lon = pd.to_numeric(d[ll[1]], errors="coerce").values
    # jenis: dari kolom bila ada, kalau tidak dari nama file
    jenis = None
    kj = next((c for c in d.columns if re.search(r"jenis|tipe|type|kategori|fclass|amenity|fungsi|peran|class",
                                                   str(c).lower())), None)
    if kj is not None:
        j = d[kj].map(norm_jenis)
        if j.notna().mean() >= .5:
            jenis = j.fillna(norm_jenis(nama_file) or "permukiman")
    if jenis is None:
        jenis = pd.Series(norm_jenis(nama_file) or "permukiman", index=d.index)
    kn = _kolom(d, r"^(nama|name|label)") or _kolom(d, r"^id")
    nama = d[kn].astype(str) if kn is not None else pd.Series([f"{j}-{i + 1}" for i, j in enumerate(jenis)], index=d.index)
    kb = _kolom(d, r"bobot|penduduk|jiwa|populasi|^pop|jumlah")
    bobot = pd.to_numeric(d[kb].astype(str).str.replace(",", ".", regex=False), errors="coerce").fillna(1.0) if kb \
        else pd.Series(1.0, index=d.index)
    out = pd.DataFrame({"nama": nama.values, "jenis": jenis.values, "lat": lat, "lon": lon, "bobot": bobot.values})
    out = out.dropna(subset=["lat", "lon"]).reset_index(drop=True)
    out["sumber"] = "file"
    return out


def od_dari_garis(d):
    rows = []
    for k, (_, r) in enumerate(d.iterrows()):
        g = r.geometry
        g = list(g.geoms)[0] if g.geom_type == "MultiLineString" else g
        if g.geom_type != "LineString":
            continue
        a, z = g.coords[0], g.coords[-1]
        rows.append({"nama": str(r.get("nama", f"Perjalanan {k + 1}")), "asal_lat": a[1], "asal_lon": a[0],
                     "tujuan_lat": z[1], "tujuan_lon": z[0], "waktu_menit": r.get("waktu_menit", np.nan),
                     "bobot": r.get("bobot", 1.0)})
    return pd.DataFrame(rows)


def rakit(items):
    """items = [(nama, peran_efektif, objek)] -> dict hasil + daftar catatan."""
    jalan, titik, gang, od, attr, cat = [], [], [], [], [], []
    for nama, peran, obj in items:
        try:
            if peran == "jalan" and isinstance(obj, gpd.GeoDataFrame):
                jalan.append((nama, obj))
            elif peran == "titik":
                titik.append(titik_dari(nama, obj))
            elif peran == "gangguan" and isinstance(obj, gpd.GeoDataFrame):
                gang.append((nama, obj))
            elif peran == "perjalanan":
                od.append(od_dari_garis(obj) if isinstance(obj, gpd.GeoDataFrame) else obj)
            elif peran == "atribut" and not isinstance(obj, gpd.GeoDataFrame):
                attr.append(obj)
            elif peran != "abaikan":
                cat.append(f"{nama}: tipe data tidak cocok untuk peran '{peran}', diabaikan.")
        except Exception as e:  # file bermasalah tidak boleh menjatuhkan aplikasi
            cat.append(f"{nama}: gagal dibaca ({e}).")
    gdf = None
    if jalan:
        jalan.sort(key=lambda x: -len(x[1]))
        gdf = jalan[0][1].reset_index(drop=True)
        for nm, _ in jalan[1:]:
            cat.append(f"{nm}: layer garis kedua diabaikan; jalan yang dipakai {jalan[0][0]}. "
                       "Ubah perannya bila itu area gangguan atau perjalanan.")
        for t in attr:
            gdf = gabung_tabel(gdf, t)
    return {"jalan": gdf, "titik": pd.concat(titik, ignore_index=True) if titik else None,
            "gangguan": gang, "od": pd.concat(od, ignore_index=True) if od else None, "catatan": cat}


def gabung_tabel(gdf, t):
    """Gabungkan tabel atribut ke jalan lewat kolom bernama sama yang nilainya unik di tabel."""
    kg = {str(c).lower(): c for c in gdf.columns if c != "geometry"}
    for c in t.columns:
        k = str(c).lower()
        if k in kg and t[c].is_unique and gdf[kg[k]].isin(t[c]).mean() >= .5:
            return gdf.merge(t, left_on=kg[k], right_on=c, how="left", suffixes=("", "_tabel"))
    return gdf


def tebak_kolom(n):
    n = n.lower()
    if re.search(r"status|fungsi|kelas|hierarki", n): return "status"
    if re.search(r"dens|kepadatan|permukiman|pemukiman|penduduk", n): return "dens"
    if re.search(r"(^|_)rs(_|$)|sakit|faskes|fasilitas|antar|konek|akses|jarak|dist", n): return "konek"
    if re.search(r"banjir|longsor|gempa|likuef|tsunami|erupsi|kering|angin|prob|^p_|_p$|hazard|bahaya|rawan|_max$", n):
        return "haz"
    if re.search(r"^(id|nama|name|ruas)", n): return "id"
    return ""


def contoh():
    """Jaringan grid kecil (bukan lokasi nyata) + titik contoh, supaya peta langsung terbuka di tempat data berada."""
    rng = np.random.default_rng(7)
    nx_, ny_ = 9, 6
    pts = [(110.30 + i * .012 + rng.random() * .004, -7.80 + j * .01 + rng.random() * .003)
           for j in range(ny_) for i in range(nx_)]
    rows = []
    for j in range(ny_):
        for i in range(nx_):
            a = j * nx_ + i
            for b in ([a + 1] if i < nx_ - 1 else []) + ([a + nx_] if j < ny_ - 1 else []):
                d = ((pts[a][1] + pts[b][1]) / 2 + 7.80) / .05
                rows.append({"id_ruas": f"R-{len(rows) + 1:03d}",
                             "fungsi": str(rng.choice(["Arteri", "Kolektor", "Lokal", "Lokal", "Lingkungan"])),
                             "p_banjir": round(float(np.clip(.75 - d * .6 + rng.random() * .3, 0, 1)), 2),
                             "p_longsor": round(float(rng.random() * .4), 2),
                             "p_gempa": round(float(.1 + rng.random() * .2), 2),
                             "n_konek": int(rng.integers(1, 6)),
                             "dens_km": round(float(20 + rng.random() * 180), 1),
                             "geometry": LineString([pts[a], pts[b]])})
    g = gpd.GeoDataFrame(rows, crs=4326)
    peran = ["rs", "rs", "posko", "depot", "quarry"] + ["permukiman"] * 8
    pilih = rng.choice(len(g), size=len(peran), replace=False)
    lab = {"rs": "RS", "posko": "Posko", "depot": "Alat berat", "quarry": "Quarry", "permukiman": "Permukiman"}
    t = []
    for k, (p, i) in enumerate(zip(peran, pilih), 1):
        x, y = g.geometry.iloc[int(i)].coords[0]
        t.append({"nama": f"{lab[p]} {k}", "jenis": p, "lat": y, "lon": x, "bobot": 1.0, "sumber": "file"})
    return g, pd.DataFrame(t), [int(i) for i in rng.choice(len(g), size=4, replace=False)]
