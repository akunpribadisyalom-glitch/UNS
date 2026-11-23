"""Ekspor hasil: GeoTIFF kedalaman maksimum, poligon genangan, dan ringkasan satu halaman (PDF). Tanpa Streamlit."""
import io

import numpy as np

BATAS_KELAS = (0.15, 0.30, 0.60)
NAMA_KELAS = ["< 15 cm", "15-30 cm", "30-60 cm", "> 60 cm"]


def geotiff_bytes(h, dx, asal, crs, minimal=0.02):
    """Kedalaman maksimum (m) -> GeoTIFF float32 (CRS UTM). Sel kering = nodata -9999."""
    import rasterio
    from rasterio.io import MemoryFile
    from rasterio.transform import from_origin
    arr = np.where(h >= minimal, h, -9999.0).astype("float32")
    with MemoryFile() as mf:
        with mf.open(driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1, dtype="float32", crs=crs,
                     transform=from_origin(asal[0], asal[1], dx, dx), nodata=-9999.0, compress="deflate") as dst:
            dst.write(arr, 1)
            dst.update_tags(1, satuan="meter", isi="kedalaman maksimum genangan")
        return mf.read()


def poligon_genangan(h, dx, asal, crs, batas=BATAS_KELAS, minimal=0.05):
    """Kedalaman maksimum -> GeoDataFrame poligon per kelas (EPSG:4326)."""
    import geopandas as gpd
    from rasterio.features import shapes
    from rasterio.transform import from_origin
    from shapely.geometry import shape
    kls = np.digitize(h, batas).astype("int16") + 1            # 1..4
    kls = np.where(h >= minimal, kls, 0).astype("int16")
    tr = from_origin(asal[0], asal[1], dx, dx)
    rows = [{"kelas": int(v), "rentang": NAMA_KELAS[int(v) - 1], "geometry": shape(g)}
            for g, v in shapes(kls, mask=kls > 0, transform=tr)]
    if not rows:
        return gpd.GeoDataFrame({"kelas": [], "rentang": []}, geometry=[], crs=4326)
    d = gpd.GeoDataFrame(rows, crs=crs).dissolve(by=["kelas", "rentang"], as_index=False)
    return d.to_crs(4326)


def ringkasan_pdf(info):
    """Ringkasan satu halaman A4 untuk pengambil keputusan.

    info: judul, skenario (dict teks), kpi [(label, nilai)], geoms (UTM), kelas (array per ruas: normal/lambat/tergenang/rusak),
    titik [(x, y, jenis)] (UTM), urutan [(no, nama, mulai_jam, selesai_jam, tim)], catatan [teks].
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    ink = colors.HexColor("#1a1f36")
    c.setFillColor(colors.HexColor("#ffd166"))
    c.rect(0, H - 70, W, 70, stroke=0, fill=1)
    c.setFillColor(ink)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(36, H - 38, info.get("judul", "Ringkasan banjir dan penanganan jalan"))
    c.setFont("Helvetica", 9)
    c.drawString(36, H - 54, "  |  ".join(f"{k}: {v}" for k, v in info.get("skenario", {}).items())[:150])

    kpi = info.get("kpi", [])
    x0, y0, bw, bh = 36, H - 130, (W - 72 - 15) / 4, 44
    for n, (lab, val) in enumerate(kpi[:8]):
        x = x0 + (n % 4) * (bw + 5)
        y = y0 - (n // 4) * (bh + 6)
        c.setStrokeColor(ink)
        c.setFillColor(colors.white)
        c.roundRect(x, y, bw, bh, 6, stroke=1, fill=1)
        c.setFillColor(ink)
        c.setFont("Helvetica-Bold", 14)
        c.drawString(x + 7, y + 24, str(val)[:16])
        c.setFont("Helvetica", 7.5)
        c.drawString(x + 7, y + 9, str(lab)[:30])

    # peta jaringan jalan (digambar langsung dari koordinat)
    mx0, my0, mw, mh = 36, 250, 300, 300
    c.setStrokeColor(ink)
    c.rect(mx0, my0, mw, mh)
    geoms = info.get("geoms", [])
    if geoms:
        xs = np.concatenate([np.asarray(g.bounds)[[0, 2]] for g in geoms if g is not None and not g.is_empty])
        ys = np.concatenate([np.asarray(g.bounds)[[1, 3]] for g in geoms if g is not None and not g.is_empty])
        gx0, gx1, gy0, gy1 = xs.min(), xs.max(), ys.min(), ys.max()
        sk = min((mw - 16) / max(gx1 - gx0, 1), (mh - 16) / max(gy1 - gy0, 1))
        ox = mx0 + (mw - sk * (gx1 - gx0)) / 2
        oy = my0 + (mh - sk * (gy1 - gy0)) / 2

        def P(x, y):
            return ox + (x - gx0) * sk, oy + (y - gy0) * sk
        warna = {"normal": "#9aa3b5", "lambat": "#f28c00", "tergenang": "#1e88e5", "rusak": "#d62828"}
        lebar = {"normal": .6, "lambat": 1.2, "tergenang": 1.6, "rusak": 2.2}
        kelas = info.get("kelas", ["normal"] * len(geoms))
        for kk in ("normal", "lambat", "tergenang", "rusak"):
            c.setStrokeColor(colors.HexColor(warna[kk]))
            c.setLineWidth(lebar[kk])
            for g, k in zip(geoms, kelas):
                if k != kk or g is None or g.is_empty:
                    continue
                for b in (g.geoms if g.geom_type == "MultiLineString" else [g]):
                    pts = [P(*p[:2]) for p in b.coords]
                    p_ = c.beginPath()
                    p_.moveTo(*pts[0])
                    for q in pts[1:]:
                        p_.lineTo(*q)
                    c.drawPath(p_, stroke=1, fill=0)
        simbol = {"rs": ("#c62828", "RS"), "posko": ("#2e7d32", "P"), "depot": ("#6a1b9a", "A"), "quarry": ("#6d4c41", "Q"),
                  "permukiman": ("#1565c0", "")}
        c.setLineWidth(.6)
        for x, y, j in info.get("titik", []):
            col, lab = simbol.get(j, ("#000000", ""))
            px, py = P(x, y)
            c.setFillColor(colors.HexColor(col))
            c.circle(px, py, 3.2 if j != "permukiman" else 1.8, stroke=0, fill=1)
            if lab:
                c.setFont("Helvetica-Bold", 6)
                c.drawString(px + 4, py + 2, lab)
    c.setLineWidth(1)
    ly = my0 - 12
    c.setFont("Helvetica", 7.5)
    lx = mx0
    for lab, col in (("normal", "#9aa3b5"), ("terganggu", "#f28c00"), ("tergenang (surut sendiri)", "#1e88e5"),
                     ("rusak (perlu perbaikan)", "#d62828")):
        c.setFillColor(colors.HexColor(col))
        c.rect(lx, ly, 14, 4, stroke=0, fill=1)
        c.setFillColor(ink)
        c.drawString(lx + 17, ly - 1, lab)
        lx += 24 + 4.2 * len(lab)

    # tabel urutan perbaikan
    tx, ty = 350, 550
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(ink)
    c.drawString(tx, ty + 6, "Urutan perbaikan (10 pertama)")
    c.setFont("Helvetica-Bold", 7.5)
    c.drawString(tx, ty - 8, "No  Ruas")
    c.drawString(tx + 150, ty - 8, "Mulai")
    c.drawString(tx + 185, ty - 8, "Selesai")
    c.drawString(tx + 228, ty - 8, "Tim")
    c.setFont("Helvetica", 7.5)
    for n, (no, nm, a, b, tm) in enumerate(info.get("urutan", [])[:10]):
        y = ty - 20 - n * 11
        c.drawString(tx, y, f"{no}")
        c.drawString(tx + 16, y, str(nm)[:34])
        c.drawString(tx + 150, y, f"{a:.0f} j")
        c.drawString(tx + 185, y, f"{b:.0f} j")
        c.drawString(tx + 228, y, str(tm)[:8])
    # catatan
    c.setFont("Helvetica-Bold", 9)
    c.drawString(36, 205, "Catatan dan batasan")
    c.setFont("Helvetica", 8)
    y = 192
    for t in info.get("catatan", []):
        for baris in _bungkus(t, 120):
            c.drawString(36, y, baris)
            y -= 10.5
        y -= 2
    c.setFont("Helvetica-Oblique", 7)
    c.drawString(36, 24, "Dibuat oleh Jalan Siaga. Model skrining (SCS-CN + aliran 2D inersia lokal); bukan pengganti kajian hidrodinamika terkalibrasi.")
    c.showPage()
    c.save()
    return buf.getvalue()


def _bungkus(t, n):
    kata, baris, cur = str(t).split(), [], ""
    for k in kata:
        if len(cur) + len(k) + 1 > n:
            baris.append(cur)
            cur = k
        else:
            cur = (cur + " " + k).strip()
    return baris + ([cur] if cur else [])
