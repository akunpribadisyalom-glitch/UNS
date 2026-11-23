"""Baca file unggahan dan tebak perannya (jalan, titik, area gangguan, perjalanan, atribut)."""
import io
import json
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
PERAN = ["otomatis", "jalan", "titik", "gangguan", "perjalanan", "atribut", "dem", "lahan", "sungai", "hujan", "jejak",
        "proyek", "abaikan"]

K_TRIP = r"perjalanan|trip|rute|(^|[^a-z])od([^a-z]|$)"
K_GANGGUAN = r"banjir|genang|longsor|gangguan|putus|rusak|terdampak|bencana|flood|landslide"
K_SUNGAI = r"sungai|river|kali|waterway|stream|saluran"
K_LAHAN = r"lahan|tutupan|landcover|land_cover|landuse|land_use|penggunaan|guna|(^|[^a-z])lc([^a-z]|$)|(^|[^a-z])cn([^a-z]|$)"
K_TINGGI = r"tinggi|kedalaman|depth|genang|jejak"
K_MM = r"(^|[^a-z])mm([^a-z]|$)|curah|hujan|rain|precip"
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
    if isinstance(obj, dict):
        return "proyek"
    if isinstance(obj, pd.DataFrame) and not isinstance(obj, gpd.GeoDataFrame):
        c = {str(x).lower().strip() for x in obj.columns}
        if {"asal_lat", "asal_lon", "tujuan_lat", "tujuan_lon"} <= c:
            return "perjalanan"
        if _latlon(obj):
            if any(re.search(K_TINGGI, x) for x in c) and not any(re.search(r"jenis|tipe|type|kategori", x) for x in c):
                return "jejak"
            return "titik"
        if any(re.search(K_MM, x) for x in c):
            return "hujan"
        return "atribut"
    tipe = set(obj.geom_type.dropna())
    if all("Line" in t for t in tipe):
        if re.search(K_TRIP, n):
            return "perjalanan"
        if re.search(K_SUNGAI, n):
            return "sungai"
        return "gangguan" if re.search(K_GANGGUAN, n) else "jalan"
    if all("Point" in t for t in tipe):
        return "titik"
    kol = [str(c).lower().strip() for c in obj.columns if c != "geometry"]
    if "cn" in kol or re.search(K_LAHAN, n):
        return "lahan"
    return "titik" if re.search(K_PERM, n) else "gangguan"


def baca_berkas(f, epsg=32749):
    """Return (peran_tebakan, objek). Objek GeoDataFrame (EPSG:4326) atau DataFrame."""
    nm = f.name.lower()
    if nm.endswith((".tif", ".tiff")):                     # DEM (raster ketinggian), dibaca nanti oleh simulasi.py
        return "dem", f.getvalue()
    if nm.endswith(".json"):                               # berkas proyek Jalan Siaga (tanda manual dan pengaturan)
        try:
            j = json.loads(f.getvalue().decode("utf-8"))
            if isinstance(j, dict) and "jalan_siaga" in j:
                return "proyek", j
        except (ValueError, UnicodeDecodeError):
            pass
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
    jalan, titik, gang, od, attr, cat, dem = [], [], [], [], [], [], []
    lahan, sungai, hujan, jejak, proyek = [], [], [], [], []
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
            elif peran == "dem" and isinstance(obj, (bytes, bytearray)):
                dem.append(bytes(obj))
            elif peran == "lahan" and isinstance(obj, gpd.GeoDataFrame):
                lahan.append(obj)
            elif peran == "sungai" and isinstance(obj, gpd.GeoDataFrame):
                sungai.append(obj)
            elif peran == "hujan" and isinstance(obj, pd.DataFrame) and not isinstance(obj, gpd.GeoDataFrame):
                hujan.append(obj)
            elif peran == "jejak" and isinstance(obj, pd.DataFrame) and not isinstance(obj, gpd.GeoDataFrame):
                jejak.append(jejak_dari(obj))
            elif peran == "proyek" and isinstance(obj, dict):
                proyek.append(obj)
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
            "gangguan": gang, "od": pd.concat(od, ignore_index=True) if od else None, "catatan": cat,
            "dem": dem[0] if dem else None, "lahan": lahan, "sungai": pd.concat(sungai, ignore_index=True) if sungai else None,
            "hujan": hujan_dari(hujan[0]) if hujan else None, "jejak": pd.concat(jejak, ignore_index=True) if jejak else None,
            "proyek": proyek[0] if proyek else None}


def hujan_dari(t):
    """Tabel curah hujan -> daftar mm per jam (urut jam bila ada kolom jam/waktu). Bisa dari prakiraan BMKG."""
    kmm = next((c for c in t.columns if re.search(K_MM, str(c).lower())), None)
    v = pd.to_numeric(t[kmm].astype(str).str.replace(",", ".", regex=False), errors="coerce")
    kj = next((c for c in t.columns if re.search(r"jam|waktu|time|hour|tanggal", str(c).lower())), None)
    if kj is not None:
        try:
            v = v.iloc[np.argsort(pd.to_numeric(t[kj], errors="coerce").to_numpy(), kind="stable")] \
                if pd.to_numeric(t[kj], errors="coerce").notna().all() else v
        except Exception:  # noqa: BLE001
            pass
    return [float(x) for x in v.fillna(0.0).clip(lower=0.0)]


def jejak_dari(t):
    """Tabel jejak banjir (lat, lon, tinggi) -> DataFrame nama, lat, lon, tinggi_m (m; nilai > 10 dianggap cm)."""
    ll = _latlon(t)
    kt = next(c for c in t.columns if re.search(K_TINGGI, str(c).lower()) and c not in ll)
    h = pd.to_numeric(t[kt].astype(str).str.replace(",", ".", regex=False), errors="coerce")
    if h.max() > 10:
        h = h / 100.0
    kn = _kolom(t, r"^(nama|name|label|lokasi)")
    nama = t[kn].astype(str) if kn is not None else pd.Series([f"Jejak {i + 1}" for i in range(len(t))], index=t.index)
    out = pd.DataFrame({"nama": nama.values, "lat": pd.to_numeric(t[ll[0]], errors="coerce").values,
                        "lon": pd.to_numeric(t[ll[1]], errors="coerce").values, "tinggi_m": h.values})
    return out.dropna().reset_index(drop=True)


def gabung_tabel(gdf, t):
    """Gabungkan tabel atribut ke jalan lewat kolom bernama sama yang nilainya unik di tabel."""
    kg = {str(c).lower(): c for c in gdf.columns if c != "geometry"}
    for c in t.columns:
        k = str(c).lower()
        if k in kg and t[c].is_unique and gdf[kg[k]].isin(t[c]).mean() >= .5:
            # kolom bernama sama di tabel (selain kunci) menggantikan kolom lama di jalan,
            # supaya unggahan baru (mis. fl_max hasil simulasi hujan) langsung dipakai.
            ganti = [x for x in t.columns if x != c and x in gdf.columns and x != "geometry"]
            gm = gdf.drop(columns=ganti).merge(t, left_on=kg[k], right_on=c, how="left", suffixes=("", "_tabel"))
            return gpd.GeoDataFrame(gm, geometry="geometry", crs=gdf.crs)
    return gdf


def tebak_kolom(n):
    n = n.lower()
    if re.search(r"status|fungsi|kelas|hierarki", n): return "status"
    if re.search(r"dens|kepadatan|permukiman|pemukiman|penduduk", n): return "dens"
    if re.search(r"(^|_)rs(_|$)|sakit|faskes|fasilitas|antar|konek|akses|jarak|dist", n): return "konek"
    if re.search(r"banjir|flood|(^|_)fl(_|$)", n): return "haz"
    if re.search(r"nama_?jalan|nm_?jalan|jalan_?nama|street|^name$|^nama$|^nm_?ruas$|nama_?ruas", n): return "jalan"
    if re.search(r"^(id|no|nomor|ruas)", n): return "id"
    return ""


def kolom_banjir(kolom):
    """Pilih kolom probabilitas banjir: 'fl_max' dulu, lalu nama yang mengandung flood/banjir."""
    for c in kolom:
        if str(c).lower().strip() == "fl_max":
            return c
    for c in kolom:
        if re.search(r"fl_?max|flood|banjir", str(c).lower()):
            return c
    return None


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
                rows.append({"no_ruas": len(rows) + 1,
                             "fungsi": str(rng.choice(["Arteri", "Kolektor", "Lokal", "Lokal", "Lingkungan"])),
                             "fl_max": round(float(np.clip(.95 - d * .9 + rng.random() * .3, 0, 1)), 2),
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
