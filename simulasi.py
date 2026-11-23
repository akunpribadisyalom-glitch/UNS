"""Simulasi genangan banjir dari hujan di atas DEM (tanpa ketergantungan Streamlit).

Alur:
  1. Hujan (pola seragam / puncak awal / tengah / akhir, atau tabel mm per jam) -> limpasan lewat SCS Curve Number
     (CN bisa seragam atau per sel dari peta tutupan lahan, disesuaikan dengan kondisi tanah kering/normal/basah).
  2. Limpasan dialirkan di permukaan DEM dengan skema inersia lokal (Bates dkk., 2010; dipakai LISFLOOD-FP) di grid 2D,
     kekasaran Manning. Air mengumpul di cekungan dan keluar lewat tepi peta.
  3. Drainase membuang genangan secara terus-menerus (mm/jam) selama masih ada air di atas bibir saluran, termasuk
     setelah hujan reda, sehingga genangan surut sendiri. Pompa membuang debit tetap di lokasi tertentu.
  4. Masukan tambahan: debit sungai (hidrograf segitiga di titik hulu) dan muka air pasang di salah satu tepi peta.
  5. Kedalaman maksimum tiap sel dan deret waktu kedalaman maksimum di sepanjang tiap ruas jalan direkam, sehingga
     jam mulai terputus, jam terbuka kembali, dan lama genangan per ruas bisa dihitung.

Ini model sederhana untuk skrining dan perencanaan, bukan pengganti model hidrodinamika terkalibrasi (HEC-RAS 2D, dll.).
"""
import io
import re

import numpy as np
import shapely

G = 9.81
# Angka Courant untuk langkah waktu (makin kecil makin teliti dan lambat). 0,7 (nilai pustaka) ternyata terlalu besar untuk
# DEM kasar dan lereng curam: air menumpuk palsu dan kedalaman bisa 2x terlalu tinggi. Di 0,5 ke bawah hasil stabil.
CFL = 0.4
CFL_TELITI = 0.25

# Kelas kelaikan jalan menurut kedalaman air (m): (terganggu >=, tergenang >=, rusak >=).
#  terganggu : masih bisa dilewati, lebih lambat.
#  tergenang : tidak bisa dilewati selama air masih setinggi itu; terbuka sendiri saat air surut (drainase).
#  rusak     : air setinggi ini membawa lumpur/sedimen dan menggerus badan jalan, sehingga jalan tetap putus
#              sesudah air surut dan perlu dibersihkan/diperbaiki (alat berat).
AMBANG_AWAL = (0.15, 0.30, 0.60)
KELAS_RUAS = ("normal", "lambat", "tergenang", "rusak")


class Dibatalkan(Exception):
    """Simulasi dihentikan pengguna."""


# ---------------------------------------------------------------- DEM
def baca_dem(data, utm_crs, batas_lonlat, maks_sel=250):
    """GeoTIFF (bytes) -> (z, dx, asal) pada grid UTM yang sama dengan jalan.

    z: array 2D (m), baris 0 = utara. dx: ukuran sel (m). asal: (x_min, y_max) UTM.
    DEM dipotong seluas area jalan + 5% lalu diproyeksikan ke UTM. Butuh paket rasterio.
    """
    import rasterio
    from rasterio.io import MemoryFile
    from rasterio.transform import array_bounds, from_origin
    from rasterio.warp import Resampling, calculate_default_transform, reproject, transform_bounds
    from rasterio.windows import Window, from_bounds

    with MemoryFile(data) as mf, mf.open() as src:
        if src.crs is None:
            raise ValueError("DEM tidak punya CRS. Beri CRS di QGIS (Layer > Set CRS) lalu simpan ulang.")
        x0, y0, x1, y1 = transform_bounds(4326, src.crs, *batas_lonlat)
        mx, my = (x1 - x0) * 0.05, (y1 - y0) * 0.05
        w = from_bounds(x0 - mx, y0 - my, x1 + mx, y1 + my, src.transform)
        c0, r0 = max(int(np.floor(w.col_off)), 0), max(int(np.floor(w.row_off)), 0)
        c1, r1 = min(int(np.ceil(w.col_off + w.width)), src.width), min(int(np.ceil(w.row_off + w.height)), src.height)
        if c1 - c0 < 2 or r1 - r0 < 2:
            raise ValueError("DEM tidak mencakup area jalan. Pastikan DEM menutupi seluruh jalan.")
        win = Window(c0, r0, c1 - c0, r1 - r0)
        arr = src.read(1, window=win).astype("float64")
        if src.nodata is not None:
            arr[arr == src.nodata] = np.nan
        arr[arr < -1000] = np.nan                       # nilai kosong tak berlabel (mis. -32768)
        tr = src.window_transform(win)
        h, wd = arr.shape
        tr_u, w_u, h_u = calculate_default_transform(src.crs, utm_crs, wd, h, *array_bounds(h, wd, tr))
        ukuran = max(abs(tr_u.a), abs(tr_u.e))
        dx = max(ukuran, max(w_u, h_u) * ukuran / maks_sel)          # perbesar sel bila grid terlalu besar
        x_min, y_max = tr_u.c, tr_u.f
        nx_, ny_ = int(np.ceil(w_u * ukuran / dx)), int(np.ceil(h_u * ukuran / dx))
        z = np.full((ny_, nx_), np.nan, dtype="float64")
        reproject(arr, z, src_transform=tr, src_crs=src.crs, dst_transform=from_origin(x_min, y_max, dx, dx),
                  dst_crs=utm_crs, resampling=Resampling.bilinear, src_nodata=np.nan, dst_nodata=np.nan)
    if np.isnan(z).all():
        raise ValueError("DEM kosong di area jalan.")
    return z, float(dx), (float(x_min), float(y_max))


def isi_kosong(z):
    """Sel NaN diisi nilai tetangga terdekat agar simulasi tidak berlubang."""
    if not np.isnan(z).any():
        return z
    from scipy.ndimage import distance_transform_edt
    idx = distance_transform_edt(np.isnan(z), return_distances=False, return_indices=True)
    return z[tuple(idx)]


def dem_contoh(batas_lonlat, ll2utm, n=150):
    """DEM sintetis (lembah sungai, dataran rendah di selatan, cekungan lokal) untuk data contoh.

    ll2utm(lon, lat) -> (x, y) UTM. Mengembalikan (z, dx, asal) di grid UTM yang sama dengan jalan.
    """
    x0, y0, x1, y1 = batas_lonlat
    px, py = ll2utm(np.array([x0, x1, x0, x1]), np.array([y0, y0, y1, y1]))
    xmin, xmax, ymin, ymax = float(np.min(px)), float(np.max(px)), float(np.min(py)), float(np.max(py))
    dx = max(xmax - xmin, ymax - ymin) / n
    nx_, ny_ = int(np.ceil((xmax - xmin) / dx)) + 1, int(np.ceil((ymax - ymin) / dx)) + 1
    yy, xx = np.mgrid[0:ny_, 0:nx_]
    v = yy / max(ny_ - 1, 1)                     # 0 = utara, 1 = selatan
    u = xx / max(nx_ - 1, 1)
    z = 6 + 55 * (1 - v) ** 1.6                                        # makin ke selatan makin rendah
    z += 3 * np.sin(u * 9) * np.cos(v * 7)                             # bergelombang halus
    z -= 4 * np.exp(-((u - .35) ** 2) / .004) * (1 - .5 * v)           # alur sungai
    z -= 3 * np.exp(-(((u - .7) ** 2) + ((v - .72) ** 2)) / .01)       # cekungan lokal
    return z, float(dx), (xmin, ymax)


def ke_lonlat(h, dx, asal, ll2utm, utm2ll):
    """Grid kedalaman UTM -> (grid pada kisi lon/lat, (barat, selatan, timur, utara)) untuk ImageOverlay (sumbu sejajar)."""
    ny, nx = h.shape
    x0, y1 = asal
    cx = x0 + np.array([0, nx * dx, 0, nx * dx])
    cy = y1 - np.array([0, 0, ny * dx, ny * dx])
    lon, lat = utm2ll(cx, cy)
    w, e, s, n = float(np.min(lon)), float(np.max(lon)), float(np.min(lat)), float(np.max(lat))
    gx = w + (np.arange(nx) + .5) / nx * (e - w)
    gy = n - (np.arange(ny) + .5) / ny * (n - s)
    LX, LY = np.meshgrid(gx, gy)
    ux, uy = ll2utm(LX.ravel(), LY.ravel())
    ix = np.floor((np.asarray(ux) - x0) / dx).astype(int).reshape(ny, nx)
    iy = np.floor((y1 - np.asarray(uy)) / dx).astype(int).reshape(ny, nx)
    ok = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
    out = np.zeros_like(h)
    out[ok] = h[iy[ok], ix[ok]]
    return out, (w, s, e, n)


# ---------------------------------------------------------------- tutupan lahan -> CN
# Curve Number (kondisi tanah normal / AMC II, kelompok tanah kira-kira C). Asumsi awal; ganti dengan kolom `cn` bila ada.
CN_LAHAN = [
    (r"jalan|aspal|beton|bandara|parkir", 98), (r"badan ?air|danau|waduk|situ|sungai|tambak|empang|kolam", 100),
    (r"industri|komersial|perdagangan|pertokoan|pergudangan|jasa", 92),
    (r"permukiman ?padat|perkotaan|kota", 92), (r"permukiman|pemukiman|perumahan|kampung|bangunan|terbangun|urban", 88),
    (r"rawa|gambut|mangrove", 90), (r"sawah|padi", 80), (r"lahan ?terbuka|tanah ?kosong|tambang|galian|lapangan", 86),
    (r"tegal|ladang|pertanian|tanaman ?semusim|palawija|lahan ?kering", 76), (r"kebun|perkebunan|tanaman ?tahunan", 70),
    (r"semak|belukar", 66), (r"rumput|padang|savana", 70), (r"hutan|vegetasi|lindung", 60),
]


def cn_dari_teks(x):
    x = str(x).lower()
    for pola, cn in CN_LAHAN:
        if re.search(pola, x):
            return float(cn)
    return np.nan


def cn_poligon(gdf):
    """GeoDataFrame lahan -> array CN per poligon (kolom `cn` bila ada, kalau tidak dari teks kelas lahan)."""
    import pandas as pd
    kol = {str(c).lower().strip(): c for c in gdf.columns if c != "geometry"}
    if "cn" in kol:
        return pd.to_numeric(gdf[kol["cn"]].astype(str).str.replace(",", ".", regex=False), errors="coerce").to_numpy()
    for c in gdf.columns:
        if c == "geometry" or gdf[c].dtype != object:
            continue
        v = gdf[c].map(cn_dari_teks)
        if v.notna().mean() >= .3:
            return v.to_numpy(dtype=float)
    return np.full(len(gdf), np.nan)


def cn_grid(lahan_utm, shape, dx, asal):
    """Daftar (GeoDataFrame UTM, array CN) -> grid CN (NaN di luar poligon) pada grid simulasi."""
    from rasterio.features import rasterize
    from rasterio.transform import from_origin
    out = np.full(shape, np.nan)
    tr = from_origin(asal[0], asal[1], dx, dx)
    for g, cn in lahan_utm:
        pairs = [(geom, float(v)) for geom, v in zip(g.geometry, cn)
                 if geom is not None and not geom.is_empty and np.isfinite(v)]
        if not pairs:
            continue
        arr = rasterize(pairs, out_shape=shape, transform=tr, fill=np.nan, dtype="float64")
        m = np.isfinite(arr)
        out[m] = arr[m]
    return out


# ---------------------------------------------------------------- hidrologi
def cn_amc(cn, kondisi="normal"):
    """Sesuaikan CN (kondisi normal) dengan kebasahan tanah sebelum hujan: kering (AMC I), normal (II), basah (III)."""
    cn = np.clip(np.asarray(cn, dtype=float), 30, 99)
    if kondisi == "kering":
        return 4.2 * cn / (10 - 0.058 * cn)
    if kondisi == "basah":
        return 23 * cn / (10 + 0.13 * cn)
    return cn


def limpasan_scs(hujan_mm, cn):
    """Kedalaman limpasan (mm) dari hujan total P dan Curve Number. Array atau skalar."""
    cn = np.clip(np.asarray(cn, dtype=float), 30, 100)
    s = 25400.0 / cn - 254.0
    ia = 0.2 * s
    p = float(hujan_mm)
    return np.where(p > ia, (p - ia) ** 2 / (p + 0.8 * s), 0.0)


POLA_HUJAN = {"seragam": "Seragam", "awal": "Puncak di awal", "tengah": "Puncak di tengah", "akhir": "Puncak di akhir"}


def profil_pola(pola, n=24):
    """Bobot hujan (jumlah 1) pada n selang waktu sama. Hujan nyata punya puncak, bukan konstan."""
    if pola == "seragam" or pola not in POLA_HUJAN:
        return np.ones(n) / n
    m = {"awal": .25, "tengah": .5, "akhir": .75}[pola]
    u = (np.arange(n) + .5) / n
    w = 0.12 + np.where(u < m, u / m, (1 - u) / (1 - m))
    return w / w.sum()


def hietograf(total_mm, durasi_jam, pola="seragam", tabel_mm=None):
    """Hujan kumulatif -> (t_detik, p_kum_mm). `tabel_mm` = curah per jam (mis. prakiraan BMKG), diskalakan ke total_mm."""
    if tabel_mm is not None and len(tabel_mm):
        v = np.clip(np.asarray(tabel_mm, dtype=float), 0, None)
        v = v * (float(total_mm) / v.sum()) if v.sum() > 0 else np.ones(len(v)) * total_mm / len(v)
        t = np.arange(len(v) + 1) * 3600.0
    else:
        n = 1 if pola == "seragam" else 24
        v = profil_pola(pola, n) * float(total_mm)
        t = np.linspace(0.0, max(float(durasi_jam), 0.05) * 3600.0, n + 1)
    return t, np.concatenate([[0.0], np.cumsum(v)])


# ---------------------------------------------------------------- sampel di sepanjang ruas
def titik_sampel(geoms_utm, shape, dx, asal, langkah=None):
    """Indeks sel (iy, ix) di sepanjang tiap ruas. `awal` = posisi awal tiap ruas pada larik terurut (untuk reduceat)."""
    x0, y1 = asal
    ny, nx = shape
    langkah = langkah or dx / 2
    IY, IX, awal, kosong, pos = [], [], [], [], 0
    for g in geoms_utm:
        awal.append(pos)
        if g is None or g.is_empty:
            IY.append(np.array([0])), IX.append(np.array([0])), kosong.append(True)
            pos += 1
            continue
        n = max(int(g.length / langkah), 2)
        p = shapely.line_interpolate_point(g, np.linspace(0.0, g.length, n + 1))
        ix = np.clip(((shapely.get_x(p) - x0) / dx).astype(int), 0, nx - 1)
        iy = np.clip(((y1 - shapely.get_y(p)) / dx).astype(int), 0, ny - 1)
        IY.append(iy), IX.append(ix), kosong.append(False)
        pos += len(iy)
    return {"iy": np.concatenate(IY), "ix": np.concatenate(IX), "awal": np.array(awal, dtype=int),
            "kosong": np.array(kosong, dtype=bool)}


def kedalaman_dari_sampel(sampel, grid, persen=90):
    """Kedalaman maksimum dan persentil sepanjang tiap ruas dari grid kedalaman. Return (maks, persentil) per ruas."""
    v = grid[sampel["iy"], sampel["ix"]]
    awal = sampel["awal"]
    maks = np.maximum.reduceat(v, awal)
    akhir = np.append(awal[1:], len(v))
    pers = np.array([np.percentile(v[a:b], persen) for a, b in zip(awal, akhir)])
    maks, pers = np.where(sampel["kosong"], 0.0, maks), np.where(sampel["kosong"], 0.0, pers)
    return maks.astype(float), pers.astype(float)


def kedalaman_ruas(geoms_utm, grid, dx, asal, langkah_m=None, persen=90):
    """Sampel kedalaman sepanjang tiap ruas. Return (maks, persentil) dalam meter (array per ruas)."""
    return kedalaman_dari_sampel(titik_sampel(geoms_utm, grid.shape, dx, asal, langkah_m), grid, persen)


def kedalaman_xy(grid, dx, asal, x, y, jendela=1):
    """Kedalaman di titik-titik (x, y UTM): rata-rata jendela (2*jendela+1)^2 sel di sekitarnya."""
    ny, nx = grid.shape
    ix = np.clip(((np.asarray(x, float) - asal[0]) / dx).astype(int), 0, nx - 1)
    iy = np.clip(((asal[1] - np.asarray(y, float)) / dx).astype(int), 0, ny - 1)
    out = []
    for a, b in zip(iy, ix):
        w = grid[max(a - jendela, 0):a + jendela + 1, max(b - jendela, 0):b + jendela + 1]
        out.append(float(w.mean()))
    return np.array(out)


# ---------------------------------------------------------------- hidrodinamika
def simulasi(z, dx, hujan_mm=100.0, durasi_jam=3.0, cn=85.0, drainase_mm_jam=0.0, manning=0.05, tambahan_jam=None,
             n_bingkai=10, progres=None, maks_langkah=80000, pola="seragam", tabel_mm=None, faktor_drainase=1.0,
             sumber=None, pasang=None, pompa=None, sampel=None, tiap_jam=0.25, batal=None, bibir=0.02, cfl=None):
    """Jalankan simulasi. Return dict: h_maks (m), bingkai, waktu_jam, seri (T x ruas), waktu_seri, neraca volume.

    cn              Curve Number (skalar atau array sebesar z), kondisi tanah sudah disesuaikan.
    drainase        kapasitas saluran (mm/jam): air genangan di atas `bibir` (m) dibuang terus, juga sesudah hujan reda.
    faktor_drainase pengali kapasitas drainase (skalar atau array sebesar z), mis. saluran dibersihkan di area tertentu.
    sumber          [{iy, ix, t_jam, q}]: debit masuk (m3/s) pada sel-sel itu, mis. sungai dari hulu.
    pasang          {sisi: U/S/T/B, t_jam, muka}: muka air (m, datum DEM) di salah satu tepi; air bisa masuk bila muka
                    lebih tinggi dari tanah tepi.
    pompa           array sebesar z: debit buang (m/s kedalaman) per sel.
    sampel          hasil titik_sampel: bila ada, kedalaman maksimum tiap ruas direkam tiap `tiap_jam` jam.
    batal           fungsi tanpa argumen; True -> simulasi berhenti (Dibatalkan).
    cfl             angka Courant (bawaan CFL di modul ini).
    """
    z = np.asarray(z, dtype="float64")
    ny, nx = z.shape
    c_ = CFL if cfl is None else float(cfl)
    th, ph = hietograf(hujan_mm, durasi_jam, pola, tabel_mm)
    durasi = float(th[-1])
    tambahan = (max(3.0, 0.5 * durasi / 3600.0) if tambahan_jam is None else float(tambahan_jam)) * 3600.0
    total_t = durasi + tambahan
    cn_ = np.clip(np.asarray(cn, dtype=float), 30, 100)

    q_prev = limpasan_scs(0.0, cn_) * 0.0
    sink = np.asarray(drainase_mm_jam, dtype=float) / 3.6e6 * np.asarray(faktor_drainase, dtype=float)
    ada_sink = bool(np.any(sink > 0))
    ada_pompa = pompa is not None and bool(np.any(pompa > 0))

    h = np.zeros_like(z)
    qx = np.zeros((ny, nx + 1))      # fluks per lebar satuan (m2/s); qx[:, k] = sisi antara kolom k-1 dan k, + = ke timur
    qy = np.zeros((ny + 1, nx))      # qy[k, :] = sisi antara baris k-1 dan k, + = ke selatan (indeks baris naik)
    h_maks = np.zeros_like(z)
    n2 = manning ** 2
    sel = dx * dx
    t, langkah = 0.0, 0
    vol_masuk = vol_keluar = vol_drain = vol_pompa = 0.0
    waktu_bingkai = np.linspace(total_t / n_bingkai, total_t, n_bingkai)
    bingkai, waktu_ok, kb = [], [], 0
    seri, waktu_seri, t_rek = [], [], 0.0
    if sampel is not None:
        seri.append(np.zeros(len(sampel["awal"]), dtype="float32"))
        waktu_seri.append(0.0)
        t_rek = tiap_jam * 3600.0

    def keluar_bebas(hb):
        return 0.5 * np.sqrt(G) * np.maximum(hb, 0.0) ** 1.5          # aliran bebas di tepi peta (m2/s)

    def sisi_pasang(zc, hc, qb, L, ke_dalam_positif):
        """Fluks di tepi dengan muka air luar L (inersia lokal). ke_dalam_positif: arah + = masuk ke domain."""
        Le = np.maximum(L, zc)
        ec = zc + hc
        hf = np.maximum(np.maximum(ec, Le) - zc, 0.0)
        grad = ((ec - Le) if ke_dalam_positif else (Le - ec)) / dx
        qn = (qb - G * hf * dt * grad) / (1.0 + G * dt * n2 * np.abs(qb) / np.maximum(hf, 1e-4) ** (7 / 3))
        # Pasang hanya boleh menahan atau memasukkan air: aliran keluar tidak pernah lebih besar dari aliran keluar bebas
        # (nilai awal qb), dan di tepi yang tanahnya masih di atas muka luar aliran keluar bebas tetap dipakai.
        sg = 1.0 if ke_dalam_positif else -1.0
        akhir = np.where(L > zc, np.maximum(sg * np.where(hf > 1e-4, qn, 0.0), sg * qb), sg * qb)
        return sg * akhir

    while t < total_t - 1e-9 and langkah < maks_langkah:
        eta = z + h
        dt = float(np.clip(c_ * dx / np.sqrt(G * max(float(h.max()), 0.02)), 0.5, 30.0))
        dt = min(dt, total_t - t)

        # --- sisi antar-sel arah x
        hf = np.maximum(np.maximum(eta[:, :-1], eta[:, 1:]) - np.maximum(z[:, :-1], z[:, 1:]), 0.0)
        grad = (eta[:, 1:] - eta[:, :-1]) / dx
        qi = qx[:, 1:-1]
        qn = (qi - G * hf * dt * grad) / (1.0 + G * dt * n2 * np.abs(qi) / np.maximum(hf, 1e-4) ** (7 / 3))
        qx[:, 1:-1] = np.where(hf > 1e-4, qn, 0.0)
        # --- sisi antar-sel arah y
        hf = np.maximum(np.maximum(eta[:-1, :], eta[1:, :]) - np.maximum(z[:-1, :], z[1:, :]), 0.0)
        grad = (eta[1:, :] - eta[:-1, :]) / dx
        qi = qy[1:-1, :]
        qn = (qi - G * hf * dt * grad) / (1.0 + G * dt * n2 * np.abs(qi) / np.maximum(hf, 1e-4) ** (7 / 3))
        qy[1:-1, :] = np.where(hf > 1e-4, qn, 0.0)
        # --- tepi peta terbuka: air keluar, tidak masuk (kecuali tepi pasang)
        qx[:, 0], qx[:, -1] = -keluar_bebas(h[:, 0]), keluar_bebas(h[:, -1])
        qy[0, :], qy[-1, :] = -keluar_bebas(h[0, :]), keluar_bebas(h[-1, :])
        if pasang is not None:
            L = float(np.interp(t / 3600.0, pasang["t_jam"], pasang["muka"]))
            s_ = pasang["sisi"]
            if s_ == "B":
                qx[:, 0] = sisi_pasang(z[:, 0], h[:, 0], qx[:, 0], L, True)
            elif s_ == "T":
                qx[:, -1] = sisi_pasang(z[:, -1], h[:, -1], qx[:, -1], L, False)
            elif s_ == "U":
                qy[0, :] = sisi_pasang(z[0, :], h[0, :], qy[0, :], L, True)
            elif s_ == "S":
                qy[-1, :] = sisi_pasang(z[-1, :], h[-1, :], qy[-1, :], L, False)

        # --- hujan (limpasan SCS kumulatif -> laju), ditambah debit sungai
        pk = float(np.interp(min(t + dt, durasi), th, ph))
        qk = limpasan_scs(pk, cn_)
        r = (qk - q_prev) / 1000.0 / dt
        q_prev = qk
        if sumber:
            rin = np.zeros_like(z)
            for s in sumber:
                q = float(np.interp(t / 3600.0, s["t_jam"], s["q"], right=0.0))
                if q > 0:
                    rin[s["iy"], s["ix"]] += q / (len(s["iy"]) * sel)
            r = r + rin

        # --- batasi pengeluaran agar sel tidak bisa mengeluarkan lebih banyak air dari isinya
        out = (np.maximum(qx[:, 1:], 0) + np.maximum(-qx[:, :-1], 0)
               + np.maximum(qy[1:, :], 0) + np.maximum(-qy[:-1, :], 0)) / dx
        tersedia = h / dt + r
        faktor = np.where(out > tersedia, tersedia / np.maximum(out, 1e-15), 1.0)
        if (faktor < 1.0).any():
            qx[:, 1:] = np.where(qx[:, 1:] > 0, qx[:, 1:] * faktor, qx[:, 1:])
            qx[:, :-1] = np.where(qx[:, :-1] < 0, qx[:, :-1] * faktor, qx[:, :-1])
            qy[1:, :] = np.where(qy[1:, :] > 0, qy[1:, :] * faktor, qy[1:, :])
            qy[:-1, :] = np.where(qy[:-1, :] < 0, qy[:-1, :] * faktor, qy[:-1, :])

        div = (qx[:, 1:] - qx[:, :-1] + qy[1:, :] - qy[:-1, :]) / dx       # > 0 = air bersih keluar
        h = np.maximum(h + dt * (r - div), 0.0)
        if ada_sink:                                                         # drainase: buang air di atas bibir saluran
            kurang = np.minimum(sink * dt, np.maximum(h - bibir, 0.0))
            h = h - kurang
            vol_drain += float(kurang.sum()) * sel
        if ada_pompa:
            kurang = np.minimum(pompa * dt, h)
            h = h - kurang
            vol_pompa += float(kurang.sum()) * sel
        np.maximum(h_maks, h, out=h_maks)
        vol_masuk += float(np.sum(r) * dt * sel * (1 if np.ndim(r) else z.size))
        vol_keluar += float(np.sum(qx[:, -1] * dx) - np.sum(qx[:, 0] * dx)
                            + np.sum(qy[-1, :] * dx) - np.sum(qy[0, :] * dx)) * dt
        t += dt
        langkah += 1
        while kb < n_bingkai and t >= waktu_bingkai[kb] - 1e-9:
            bingkai.append(h.astype("float32"))
            waktu_ok.append(waktu_bingkai[kb] / 3600.0)
            kb += 1
        if sampel is not None:
            while t >= t_rek - 1e-9 and t_rek <= total_t + 1e-9:
                seri.append(np.maximum.reduceat(h[sampel["iy"], sampel["ix"]], sampel["awal"]).astype("float32"))
                waktu_seri.append(t_rek / 3600.0)
                t_rek += tiap_jam * 3600.0
        if langkah % 50 == 0:
            if batal is not None and batal():
                raise Dibatalkan()
            if progres:
                progres(min(t / total_t, 1.0))

    while kb < n_bingkai:                                   # bila langkah dibatasi, isi sisa bingkai
        bingkai.append(h.astype("float32"))
        waktu_ok.append(waktu_bingkai[kb] / 3600.0)
        kb += 1
    seri_arr = None
    if sampel is not None:
        if waktu_seri[-1] < total_t / 3600.0 - 1e-6:        # rekam keadaan akhir
            seri.append(np.maximum.reduceat(h[sampel["iy"], sampel["ix"]], sampel["awal"]).astype("float32"))
            waktu_seri.append(total_t / 3600.0)
        seri_arr = np.stack(seri)
        seri_arr[:, sampel["kosong"]] = 0.0
    if progres:
        progres(1.0)
    return {"h_maks": h_maks, "bingkai": bingkai, "waktu_jam": waktu_ok, "langkah": langkah,
            "limpasan_mm": float(np.mean(qk)) if langkah else 0.0, "selesai": t >= total_t - 1e-6,
            "vol_masuk": vol_masuk, "vol_keluar": vol_keluar, "vol_drain": vol_drain, "vol_pompa": vol_pompa,
            "vol_tersimpan": float(h.sum() * sel), "seri": seri_arr, "waktu_seri": np.array(waktu_seri),
            "hujan_total_mm": float(ph[-1]), "hujan_jam": durasi / 3600.0, "horizon_jam": total_t / 3600.0,
            "h_akhir": h.astype("float32")}


# ---------------------------------------------------------------- klasifikasi ruas
def kelas_ruas(d, ambang=AMBANG_AWAL):
    """Kedalaman (m) -> 'rusak' / 'tergenang' / 'lambat' / 'normal'."""
    t, g, r = ambang
    d = np.asarray(d, dtype=float)
    return np.where(d >= r, "rusak", np.where(d >= g, "tergenang", np.where(d >= t, "lambat", "normal")))


def jendela_waktu(seri, waktu, ambang):
    """Dari deret waktu kedalaman per ruas (T x N): jam mulai, jam air turun lagi di bawah `ambang`, lama (jam).

    belum = True bila sampai akhir simulasi air masih di atas ambang (genangan belum surut dalam waktu pantau).
    """
    seri = np.asarray(seri)
    waktu = np.asarray(waktu, dtype=float)
    T = len(waktu)
    m = seri >= ambang
    ada = m.any(axis=0)
    pertama = np.argmax(m, axis=0)
    terakhir = T - 1 - np.argmax(m[::-1], axis=0)
    dt = float(np.diff(waktu).mean()) if T > 1 else 0.0
    return {"ada": ada, "mulai": np.where(ada, waktu[pertama], np.nan),
            "selesai": np.where(ada, waktu[np.minimum(terakhir + 1, T - 1)], np.nan),
            "durasi": m.sum(axis=0) * dt, "belum": ada & (terakhir >= T - 1)}


def prob_dari_kedalaman(d, penuh=0.5):
    """Kedalaman (m) -> pseudo-probabilitas 0-1 (kedalaman >= `penuh` meter dianggap 1) agar bisa diunggah sebagai fl_max."""
    return np.clip(np.asarray(d, dtype=float) / penuh, 0, 1)


def gambar_rgba(h, minimal=0.02, penuh=1.5):
    """Kedalaman -> citra RGBA (biru muda -> biru tua -> ungu) untuk ImageOverlay peta. Sel kering transparan."""
    f = np.clip((h - minimal) / (penuh - minimal), 0, 1)
    rgba = np.zeros(h.shape + (4,), dtype=np.uint8)
    peta = np.array([[0.00, 150, 210, 255], [0.35, 40, 120, 230], [0.70, 20, 50, 170], [1.00, 110, 20, 140]])
    for k in range(3):
        rgba[..., k] = np.interp(f, peta[:, 0], peta[:, k + 1]).astype(np.uint8)
    rgba[..., 3] = np.where(h >= minimal, np.interp(f, [0, .15, 1], [110, 170, 215]), 0).astype(np.uint8)
    return rgba


def png_bytes(rgba):
    from PIL import Image
    b = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(b, format="PNG")
    return b.getvalue()
