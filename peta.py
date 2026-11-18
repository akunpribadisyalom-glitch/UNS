"""Peta Folium bergaya kartun: jalan berkelas, gangguan bergaris tepi, lencana urutan, pin emoji."""
import folium
import numpy as np
import pandas as pd
from folium.plugins import Draw

import gaya
import scoring as sc

TILE = "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png"
SAT = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
KRUS = {"Sangat kritis": ("#7b5cff", 6), "Tinggi": ("#4c8dff", 4.5), "Sedang": ("#4cc9f0", 3.2), "Rendah": ("#aebbd0", 2.2)}
EMOJI = {"rs": "🏥", "posko": "⛺", "depot": "🚜", "quarry": "⛏️", "permukiman": "🏠"}
LABEL = {"rs": "RS", "posko": "Posko", "depot": "Alat berat", "quarry": "Quarry", "permukiman": "Permukiman"}


def dasar(gdf, view=None):
    if view:
        m = folium.Map(location=view[0], zoom_start=view[1], tiles=None, control_scale=True)
    else:
        b = gdf.total_bounds
        span = max(b[2] - b[0], (b[3] - b[1]) * 1.3, 1e-5)
        z = int(np.clip(np.floor(np.log2(1100 / span)), 3, 18))      # perkiraan zoom agar data muat
        m = folium.Map(location=[(b[1] + b[3]) / 2, (b[0] + b[2]) / 2], zoom_start=z, tiles=None, control_scale=True)
        m.fit_bounds([[b[1], b[0]], [b[3], b[2]]])
    folium.TileLayer(TILE, attr="© OpenStreetMap, © CARTO", name="Peta", max_zoom=19).add_to(m)
    folium.TileLayer(SAT, attr="Esri", name="Citra satelit", max_zoom=19).add_to(m)
    return m


def _geo(m, d, warna, lebar, tip, dash=None, op=.95, fields=("nama", "kelas"), alias=("Ruas", "Kelas")):
    if d.empty:
        return
    folium.GeoJson(
        d, style_function=lambda _, c=warna, w=lebar, ds=dash, o=op: {
            "color": c, "weight": w, "opacity": o, "dashArray": ds, "lineCap": "round", "lineJoin": "round"},
        tooltip=folium.GeoJsonTooltip(fields=list(fields), aliases=list(alias), sticky=False) if tip else None,
    ).add_to(m)


def jalan_krusial(m, g):
    for k in reversed(sc.KELAS):                       # kelas rendah dulu, kritis di atas
        sub = g[g["kelas"] == k][["nama", "kelas", "skor", "geometry"]].copy()
        sub["skor"] = sub["skor"].round(3)
        w, lb = KRUS[k]
        _geo(m, sub, w, lb, True, fields=("nama", "kelas", "skor"), alias=("Ruas", "Kelas", "Skor"))


def gangguan(m, gdf, nama, P, L, rep=frozenset()):
    """Gangguan 'stiker': garis tepi navy + isi warna."""
    d = gdf[["geometry"]].copy()
    d["nama"], d["kelas"] = nama.values, ""
    for ids, warna, lbl in ((sorted(L), gaya.AMBER, "terganggu"), (sorted(P - set(rep)), gaya.KORAL, "putus"),
                            (sorted(set(rep) & set(P)), gaya.HIJAU, "diperbaiki")):
        if not ids:
            continue
        sub = d.loc[ids].copy()
        sub["kelas"] = lbl
        _geo(m, sub, gaya.NAVY, 10, False)
        _geo(m, sub, warna, 6, True)


def _ikon(html, lebar=34):
    return folium.DivIcon(html=html, icon_size=(lebar, lebar), icon_anchor=(lebar // 2, lebar // 2))


def lencana(m, gdf, urut):
    """Nomor urutan perbaikan di tengah tiap ruas putus."""
    for i, n in urut.items():
        p = gdf.geometry.iloc[i].interpolate(.5, normalized=True)
        h = (f"<div style='width:30px;height:30px;border-radius:50%;background:{gaya.MATAHARI};border:3px solid {gaya.NAVY};"
             f"display:flex;align-items:center;justify-content:center;font:800 14px Nunito,sans-serif;color:{gaya.NAVY};"
             f"box-shadow:2px 2px 0 {gaya.NAVY}'>{n}</div>")
        folium.Marker([p.y, p.x], icon=_ikon(h, 30), tooltip=f"Urutan ke-{n}").add_to(m)


def pin(m, titik):
    for _, r in titik.iterrows():
        j = str(r["jenis"])
        h = (f"<div style='width:36px;height:36px;border-radius:50%;background:#fff;border:3px solid {gaya.NAVY};"
             f"display:flex;align-items:center;justify-content:center;font-size:19px;box-shadow:3px 3px 0 {gaya.NAVY}'>"
             f"{EMOJI.get(j, '📍')}</div>")
        folium.Marker([r["lat"], r["lon"]], icon=_ikon(h, 36),
                      tooltip=f"{LABEL.get(j, j)}: {r['nama']}").add_to(m)


def alat_gambar(m):
    Draw(export=False, position="topleft", edit_options={"edit": False},
         draw_options={"polygon": {"shapeOptions": {"color": gaya.KORAL}}, "rectangle": {"shapeOptions": {"color": gaya.KORAL}},
                       "polyline": False, "circle": False, "marker": False, "circlemarker": False}).add_to(m)


def garis_garis(m, gdf, idx, warna, w, ket, dash=None, op=.9):
    for i in idx:
        folium.GeoJson(gdf.geometry.iloc[i].__geo_interface__, tooltip=ket,
                       style_function=lambda _, c=warna, w=w, ds=dash, o=op:
                       {"color": c, "weight": w, "opacity": o, "dashArray": ds, "lineCap": "round"}).add_to(m)


def tempuh(m, gdf, M, tbl, jalur, pilih):
    """Garis asal-tujuan berwarna menurut keterlambatan + rute perjalanan terpilih."""
    for k, (nm, o, t, _) in enumerate(M.od):
        r = tbl.iloc[k]
        x = r["pulih_menit"] / r["normal_menit"] if pd.notna(r["pulih_menit"]) and r["normal_menit"] else np.nan
        warna = "#1d2b4f" if np.isnan(x) else "#2ecc71" if x <= 1.1 else "#ffcc00" if x <= 1.5 else \
            "#ff9f1c" if x <= 2.5 else "#ff4d4d"
        ket = f"{nm}: normal {r['normal_menit']:.1f} mnt, sekarang " + \
              ("terputus" if np.isnan(x) else f"{r['pulih_menit']:.1f} mnt")
        folium.PolyLine([(o[1], o[0]), (t[1], t[0])], color=warna, weight=4, opacity=.9, tooltip=ket,
                        line_cap="round").add_to(m)
    j = jalur[pilih]
    garis_garis(m, gdf, j["normal"], "#4c8dff", 11, "rute normal", op=.35)
    garis_garis(m, gdf, j["terdampak"], gaya.UNGU, 6, "rute terdampak")
    garis_garis(m, gdf, j["pulih"], gaya.HIJAU, 3, "rute sekarang")
