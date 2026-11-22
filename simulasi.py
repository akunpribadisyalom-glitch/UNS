"""Simulasi genangan banjir dari curah hujan di atas DEM (tanpa ketergantungan Streamlit).

Alur:
  1. Hujan (mm) -> limpasan efektif lewat SCS Curve Number (CN) dikurangi kapasitas drainase (mm/jam).
  2. Limpasan dialirkan di permukaan DEM dengan skema inersia lokal (Bates dkk., 2010; dipakai LISFLOOD-FP)
     di grid 2D, dengan kekasaran Manning. Air mengumpul di cekungan dan keluar lewat tepi peta.
  3. Kedalaman maksimum tiap sel diambil sepanjang simulasi, lalu disampling di sepanjang tiap ruas jalan.

Ini model sederhana untuk skrining dan perencanaan, bukan pengganti model hidrodinamika terkalibrasi (HEC-RAS 2D, dll.).
"""
import io

import numpy as np

G = 9.81

# Kelas kelaikan jalan menurut kedalaman air (m). Bisa diubah di aplikasi.
AMBANG_AWAL = (0.15, 0.30)   # (terganggu >=, putus >=)


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


# ---------------------------------------------------------------- hidrologi
def limpasan_scs(hujan_mm, cn):
    """Kedalaman limpasan (mm) dari hujan total P dan Curve Number. Array atau skalar."""
    cn = np.clip(np.asarray(cn, dtype=float), 30, 100)
    s = 25400.0 / cn - 254.0
    ia = 0.2 * s
    p = float(hujan_mm)
    return np.where(p > ia, (p - ia) ** 2 / (p + 0.8 * s), 0.0)


# ---------------------------------------------------------------- hidrodinamika
def simulasi(z, dx, hujan_mm, durasi_jam, cn=85.0, drainase_mm_jam=0.0, manning=0.05, tambahan_jam=None,
             n_bingkai=8, progres=None, maks_langkah=60000):
    """Jalankan simulasi. Return dict: h_maks (m), bingkai (kedalaman pada waktu merata), waktu_jam, neraca volume.

    cn           Curve Number (scalar atau array sebesar z). Kota padat ~90-98, lahan campuran ~80, sawah/hutan ~60-70.
    drainase     kapasitas saluran/infiltrasi tambahan (mm/jam) yang menyerap sebagian hujan.
    tambahan_jam waktu setelah hujan reda untuk melihat surut/genangan bertahan (bawaan 50% durasi hujan).
    """
    z = np.asarray(z, dtype="float64")
    ny, nx = z.shape
    durasi = max(float(durasi_jam), 0.05) * 3600.0
    tambahan = (0.5 * durasi_jam if tambahan_jam is None else tambahan_jam) * 3600.0
    total_t = durasi + tambahan

    # limpasan SCS dari hujan total; drainase mengurangi limpasan sebanding durasi hujan
    q_tot = limpasan_scs(hujan_mm, cn) * np.ones_like(z)                 # mm per sel selama hujan
    kurang = drainase_mm_jam * durasi_jam * (q_tot / max(float(hujan_mm), 1e-9))
    q_eff = np.clip(q_tot - kurang, 0.0, None)
    r_hujan = (q_eff / 1000.0) / durasi                                  # m/detik per sel selama hujan

    h = np.zeros_like(z)
    qx = np.zeros((ny, nx + 1))      # fluks per lebar satuan (m2/s); qx[:, k] = sisi antara kolom k-1 dan k, + = ke timur
    qy = np.zeros((ny + 1, nx))      # qy[k, :] = sisi antara baris k-1 dan k, + = ke selatan (indeks baris naik)
    h_maks = np.zeros_like(z)
    n2 = manning ** 2
    sel = dx * dx
    t, langkah = 0.0, 0
    vol_masuk = vol_keluar = 0.0
    waktu_bingkai = np.linspace(total_t / n_bingkai, total_t, n_bingkai)
    bingkai, waktu_ok, kb = [], [], 0

    def keluar_bebas(hb):
        return 0.5 * np.sqrt(G) * np.maximum(hb, 0.0) ** 1.5          # aliran bebas di tepi peta (m2/s)

    while t < total_t - 1e-9 and langkah < maks_langkah:
        eta = z + h
        dt = float(np.clip(0.7 * dx / np.sqrt(G * max(float(h.max()), 0.02)), 0.5, 30.0))
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
        # --- tepi peta terbuka: air keluar, tidak masuk
        qx[:, 0], qx[:, -1] = -keluar_bebas(h[:, 0]), keluar_bebas(h[:, -1])
        qy[0, :], qy[-1, :] = -keluar_bebas(h[0, :]), keluar_bebas(h[-1, :])

        # --- batasi pengeluaran agar sel tidak bisa mengeluarkan lebih banyak air dari isinya
        r = r_hujan if t < durasi else 0.0
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
        np.maximum(h_maks, h, out=h_maks)
        vol_masuk += float(np.sum(r)) * dt * sel if np.ndim(r) else r * dt * sel * z.size
        vol_keluar += float(np.sum(qx[:, -1] * dx) - np.sum(qx[:, 0] * dx)
                            + np.sum(qy[-1, :] * dx) - np.sum(qy[0, :] * dx)) * dt
        t += dt
        langkah += 1
        while kb < n_bingkai and t >= waktu_bingkai[kb] - 1e-9:
            bingkai.append(h.astype("float32"))
            waktu_ok.append(waktu_bingkai[kb] / 3600.0)
            kb += 1
        if progres and langkah % 50 == 0:
            progres(min(t / total_t, 1.0))

    while kb < n_bingkai:                                   # bila langkah dibatasi, isi sisa bingkai
        bingkai.append(h.astype("float32"))
        waktu_ok.append(waktu_bingkai[kb] / 3600.0)
        kb += 1
    return {"h_maks": h_maks, "bingkai": bingkai, "waktu_jam": waktu_ok, "langkah": langkah,
            "limpasan_mm": float(np.mean(q_eff)), "selesai": t >= total_t - 1e-6,
            "vol_masuk": vol_masuk, "vol_keluar": vol_keluar, "vol_tersimpan": float(h.sum() * sel)}


# ---------------------------------------------------------------- ruas jalan
def kedalaman_ruas(geoms_utm, grid, dx, asal, langkah_m=None, persen=90):
    """Sampel kedalaman sepanjang tiap ruas. Return (maks, persentil) dalam meter (array per ruas)."""
    x0, y1 = asal
    ny, nx = grid.shape
    langkah = langkah_m or dx / 2
    maks, pers = [], []
    for g in geoms_utm:
        if g is None or g.is_empty:
            maks.append(0.0), pers.append(0.0)
            continue
        n = max(int(g.length / langkah), 2)
        pts = [g.interpolate(i / n, normalized=True) for i in range(n + 1)]
        cx = np.clip(((np.array([p.x for p in pts]) - x0) / dx).astype(int), 0, nx - 1)
        cy = np.clip(((y1 - np.array([p.y for p in pts])) / dx).astype(int), 0, ny - 1)
        v = grid[cy, cx]
        maks.append(float(v.max())), pers.append(float(np.percentile(v, persen)))
    return np.array(maks), np.array(pers)


def kelas_kedalaman(d, ambang=AMBANG_AWAL):
    """Kedalaman (m) -> 'putus' / 'lambat' / 'normal' (sama dengan status jalan di aplikasi)."""
    t, p = ambang
    return np.where(d >= p, "putus", np.where(d >= t, "lambat", "normal"))


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
