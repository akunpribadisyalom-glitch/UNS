"""Peta Folium: jalan diwarnai merah/kuning/hijau menurut probabilitas banjir, gangguan bertepi gelap, lencana urutan, pin emoji."""
import folium
import numpy as np
import pandas as pd
from folium.plugins import Draw

import gaya
import scoring as sc

TILE = "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png"
SAT = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
# Warna jalan menurut probabilitas banjir: merah (rawan), kuning (sedang), hijau (aman).
KRUS = {k: (sc.WARNA[k], sc.LEBAR[k]) for k in list(sc.KELAS) + list(sc.KELAS_PERSEN)}
EMOJI = {"rs": "🏥", "posko": "⛺", "depot": "🚜", "quarry": "⛏️", "permukiman": "🏠", "sungai": "🌊", "pompa": "🔧",
         "drainase": "🕳️", "tinggikan": "⬆️"}
LABEL = {"rs": "RS", "posko": "Posko", "depot": "Alat berat", "quarry": "Quarry", "permukiman": "Permukiman",
         "sungai": "Masukan sungai", "pompa": "Pompa", "drainase": "Drainase dibersihkan", "tinggikan": "Ruas ditinggikan"}


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


def jalan_krusial(m, g, urutan=None):
    """Jalan diwarnai menurut kelas banjir. Tiap garis diberi tepi gelap tipis agar kuning/hijau tetap terbaca."""
    for k in reversed(urutan or sc.KELAS):             # hijau dulu, merah di atas
        ada_tinggi = "banjir_m" in g.columns
        sub = g[g["kelas"] == k][["nama", "kelas", "skor"] + (["banjir_m"] if ada_tinggi else []) + ["geometry"]].copy()
        if sub.empty:
            continue
        sub["skor"] = sub["skor"].round(3)
        w, lb = KRUS[k]
        f_, a_ = ["nama", "kelas", "skor"], ["Ruas", "Kelas", "Probabilitas/skor"]
        if ada_tinggi:
            sub["banjir_m"] = sub["banjir_m"].round(2)
            f_.append("banjir_m"), a_.append("Tinggi banjir simulasi (m)")
            for kol, lb_ in (("putus_mulai_jam", "Mulai terputus (jam)"), ("putus_lama_jam", "Lama terputus (jam)"),
                             ("genang_lama_jam", "Lama genangan (jam)")):
                if kol in g.columns:
                    sub[kol] = g.loc[sub.index, kol].round(1).astype(object).where(g.loc[sub.index, kol].notna(), "-")
                    f_.append(kol), a_.append(lb_)
        _geo(m, sub, gaya.CASING, lb + 2.5, False, op=.55)
        _geo(m, sub, w, lb, True, fields=tuple(f_), alias=tuple(a_))


def gangguan(m, gdf, nama, P, L, rep=frozenset(), T=()):
    """Rusak = merah putus-putus tebal bertepi gelap; tergenang = biru putus-putus; terganggu = oranye; diperbaiki = hijau penuh."""
    d = gdf[["geometry"]].copy()
    d["nama"], d["kelas"] = nama.values, ""
    for ids, warna, lbl, dash in ((sorted(L), gaya.AMBER, "terganggu", "3 9"),
                                  (sorted(set(T) - set(P)), gaya.AIR, "tergenang (surut sendiri)", "6 7"),
                                  (sorted(P - set(rep)), gaya.KORAL, "rusak", "10 8"),
                                  (sorted(set(rep) & set(P)), gaya.HIJAU, "diperbaiki", None)):
        if not ids:
            continue
        sub = d.loc[ids].copy()
        sub["kelas"] = lbl
        _geo(m, sub, "#ffffff", 13, False, op=.95)               # alas putih supaya menonjol dari jalan di bawahnya
        _geo(m, sub, gaya.CASING, 10, False, op=.9)
        _geo(m, sub, warna, 6, True, dash=dash)


def _ikon(html, lebar=34):
    return folium.DivIcon(html=html, icon_size=(lebar, lebar), icon_anchor=(lebar // 2, lebar // 2))


def lencana(m, gdf, urut):
    """Nomor urutan perbaikan di tengah tiap ruas putus."""
    for i, n in urut.items():
        p = gdf.geometry.iloc[i].interpolate(.5, normalized=True)
        h = (f"<div style='width:30px;height:30px;border-radius:50%;background:#fff;border:3px solid {gaya.INK};"
             f"display:flex;align-items:center;justify-content:center;font:800 14px Nunito,sans-serif;color:{gaya.INK};"
             f"box-shadow:2px 2px 0 {gaya.INK}'>{n}</div>")
        folium.Marker([p.y, p.x], icon=_ikon(h, 30), tooltip=f"Urutan ke-{n}").add_to(m)


def pin(m, titik):
    for _, r in titik.iterrows():
        j = str(r["jenis"])
        h = (f"<div style='width:36px;height:36px;border-radius:50%;background:#fff;border:3px solid {gaya.INK};"
             f"display:flex;align-items:center;justify-content:center;font-size:19px;box-shadow:3px 3px 0 {gaya.INK}'>"
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
        warna = gaya.UNGU if np.isnan(x) else "#2e9d57" if x <= 1.1 else "#f2b705" if x <= 1.5 else \
            "#f28c00" if x <= 2.5 else "#d62828"
        ket = f"{nm}: normal {r['normal_menit']:.1f} mnt, sekarang " + \
              ("terputus" if np.isnan(x) else f"{r['pulih_menit']:.1f} mnt")
        folium.PolyLine([(o[1], o[0]), (t[1], t[0])], color=gaya.CASING, weight=7, opacity=.6, line_cap="round").add_to(m)
        folium.PolyLine([(o[1], o[0]), (t[1], t[0])], color=warna, weight=4, opacity=1, tooltip=ket,
                        line_cap="round").add_to(m)
    j = jalur[pilih]
    garis_garis(m, gdf, j["normal"], gaya.KERJA, 11, "rute normal", op=.35)
    garis_garis(m, gdf, j["terdampak"], gaya.UNGU, 6, "rute terdampak")
    garis_garis(m, gdf, j["pulih"], gaya.HIJAU, 3, "rute sekarang")


def genangan(m, png, batas, nama="Genangan banjir (simulasi)"):
    """Lapisan genangan hasil simulasi. png = bytes PNG RGBA, batas = (barat, selatan, timur, utara) lon/lat."""
    import base64
    w, s_, e, n = batas
    uri = "data:image/png;base64," + base64.b64encode(png).decode()
    folium.raster_layers.ImageOverlay(image=uri, bounds=[[s_, w], [n, e]], opacity=.85, name=nama,
                                      interactive=False, zindex=5).add_to(m)


def fase(m, gdf, nama, blok, lambat, evak=None, perm=None, iso=None):
    """Keadaan jaringan pada satu jam: ruas terblokir (biru), terganggu (oranye), jalur evakuasi (hijau), permukiman terisolasi (merah)."""
    d = gdf[["geometry"]].copy()
    d["nama"], d["kelas"] = nama.values, ""
    for ids, warna, lbl, dash in ((sorted(lambat), gaya.AMBER, "terganggu", "3 9"), (sorted(blok), gaya.AIR, "terblokir", "6 7")):
        if not ids:
            continue
        sub = d.loc[ids].copy()
        sub["kelas"] = lbl
        _geo(m, sub, "#ffffff", 13, False, op=.95)
        _geo(m, sub, gaya.CASING, 10, False, op=.9)
        _geo(m, sub, warna, 6, True, dash=dash)
    if evak is not None and len(evak):
        for r in evak.itertuples():
            if r.tersambung and len(r.ruas):
                garis_garis(m, gdf, r.ruas, gaya.HIJAU, 4, f"Evakuasi {r.permukiman}: {r.menit:.0f} menit", op=.9)
    if perm is not None and iso is not None:
        for r, bad in zip(perm.itertuples(), iso):
            if bad:
                folium.CircleMarker([r.lat, r.lon], radius=8, color=gaya.KORAL, weight=3, fill=True, fill_color="#ffffff",
                                    fill_opacity=.9, tooltip=f"{r.nama}: terisolasi dari RS").add_to(m)


def sungai_garis(m, gdf):
    folium.GeoJson(gdf[["geometry"]], style_function=lambda _: {"color": "#4cc9f0", "weight": 3, "opacity": .9},
                   name="Sungai").add_to(m)


def masukan(m, df):
    """Penanda masukan sungai dan intervensi (pompa, drainase, ruas ditinggikan)."""
    for _, r in df.iterrows():
        j = str(r["jenis"])
        h = (f"<div style='width:34px;height:34px;border-radius:50%;background:#e3f6ff;border:3px solid {gaya.INK};"
             f"display:flex;align-items:center;justify-content:center;font-size:18px;box-shadow:3px 3px 0 {gaya.INK}'>"
             f"{EMOJI.get(j, '📍')}</div>")
        folium.Marker([r["lat"], r["lon"]], icon=_ikon(h, 34), tooltip=f"{LABEL.get(j, j)}: {r.get('nama', '')}").add_to(m)
