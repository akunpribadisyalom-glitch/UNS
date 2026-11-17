"""Tab Streamlit: simulasi gangguan jalan, urutan perbaikan, dan perbandingan waktu tempuh.

Memakai restorasi.py (model & algoritma). Semua hasil hitung disimpan di st.session_state
supaya klik di peta tidak memicu hitung ulang algoritma.
"""
import hashlib
import json

import branca.colormap as bcm
import folium
import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
import streamlit as st
from folium.plugins import Draw
from shapely.geometry import Point, shape
from streamlit_folium import st_folium

import restorasi as rt
import scoring as sc

TITIK_COLS = ["nama", "jenis", "lat", "lon", "bobot"]
JENIS = {"banjir": "Banjir", "longsor": "Longsor", "gempa": "Gempa bumi", "umum": "Umum"}
LABEL_METRIK = {
    "rs": "Akses ke rumah sakit", "posko": "Akses ke posko", "depot": "Akses alat berat",
    "konek": "Keterhubungan jaringan", "od": "Waktu tempuh perjalanan", "krus": "Krusialitas ruas",
}
CATATAN = {
    "banjir": "Banjir: akses warga ke **rumah sakit** dan posko diutamakan; alat berat tidak wajib "
              "(ruas tergenang umumnya hanya terganggu, bukan tertimbun).",
    "longsor": "Longsor: ruas tertimbun, jadi **alat berat dari depot harus bisa menjangkau ruas** lebih dulu. "
               "Perbaikan diurutkan mengikuti akses depot, lalu RS/posko.",
    "gempa": "Gempa: gabungan akses RS, alat berat, dan keterhubungan jaringan.",
    "umum": "Umum: seimbang, waktu tempuh perjalanan dan keterhubungan lebih berat.",
}
NAMA_ALGO = {
    "greedy": "Greedy", "sa": "Simulated annealing", "optimal": "Optimal (DP)",
    "krus": "Urut krusialitas", "acak": "Acak (pembanding)",
}
WARNA_TITIK = {"rs": "red", "posko": "blue", "depot": "orange", "permukiman": "green"}

_BACA = None   # fungsi baca file geo dari app.py (di-cache di sana)


# ------------------------------------------------------------------ util
@st.cache_resource(show_spinner="Membangun graf jaringan jalan...")
def _graf(_g, kelas, kec, sig, desimal):
    return rt.bangun_graf(_g, list(kelas), dict(kec), desimal)


def _md5(*a):
    return hashlib.md5(repr(a).encode()).hexdigest()[:10]


def _terapkan(idx, status):
    P = set(st.session_state["rt_ms_putus"])
    L = set(st.session_state["rt_ms_lambat"])
    idx = {int(i) for i in idx}
    P -= idx
    L -= idx
    if status == "putus":
        P |= idx
    elif status == "lambat":
        L |= idx
    st.session_state["rt_ms_putus"] = sorted(P)
    st.session_state["rt_ms_lambat"] = sorted(L)


def _kena(geoms, buf=0.0):
    """Indeks ruas yang beririsan dengan geometri (EPSG:4326). Titik/garis diberi buffer minimal 20 m."""
    utm = st.session_state["rt_utm"]
    gs = gpd.GeoSeries(list(geoms), crs=4326).to_crs(utm.crs)
    hit = set()
    for g in gs:
        if g is None or g.is_empty:
            continue
        if g.geom_type not in ("Polygon", "MultiPolygon") or buf > 0:
            g = g.buffer(max(buf, 20.0))
        hit |= {int(i) for i in utm.index[utm.geometry.intersects(g)]}
    return sorted(hit)


def _terdekat(klik):
    if not klik:
        return None
    utm = st.session_state["rt_utm"]
    p = gpd.GeoSeries([Point(klik["lng"], klik["lat"])], crs=4326).to_crs(utm.crs).iloc[0]
    d = utm.geometry.distance(p)
    return int(d.idxmin()) if d.min() < 100 else None


# ---- callback tombol (jalan sebelum skrip dijalankan ulang, jadi aman mengubah state widget)
def _cb_klik(status):
    s = st.session_state.get("rt_sel")
    if s is not None:
        _terapkan([s], status)


def _cb_area(status):
    gs = [shape(f["geometry"]) for f in st.session_state.get("rt_draw", []) if f.get("geometry")]
    if gs:
        _terapkan(_kena(gs), status)


def _cb_unggah(default, buf):
    f = st.session_state.get("rt_up_gang")
    if f is None:
        return
    d = _BACA(f.name, f.getvalue())
    d = (d.set_crs(4326) if d.crs is None else d).to_crs(4326)
    kol = next((c for c in d.columns if str(c).lower() in ("status", "kondisi", "gangguan")), None)
    pu, la = [], []
    for _, r in d.iterrows():
        s = default
        if kol is not None:
            v = str(r[kol]).lower()
            if any(x in v for x in ("putus", "rusak", "tutup", "timbun")):
                s = "putus"
            elif any(x in v for x in ("ganggu", "lambat", "genang", "banjir")):
                s = "lambat"
        (pu if s == "putus" else la).extend(_kena([r.geometry], buf))
    _terapkan(la, "lambat")
    _terapkan(pu, "putus")


def _cb_acak():
    s, hz = st.session_state, st.session_state["rt_hz"]
    z = hz[s["rt_hz_pilih"]].to_numpy(dtype=float)
    z = np.nan_to_num(z)
    p = ((z - z.min()) / ((z.max() - z.min()) or 1.0) + 0.05) ** 2
    p = p / p.sum()
    n = len(p)
    rng = np.random.default_rng(int(s["rt_seed"]))
    kp, kl = int(min(s["rt_n_putus"], n)), int(s["rt_n_lambat"])
    ip = rng.choice(n, size=kp, replace=False, p=p)
    sisa = np.setdiff1d(np.arange(n), ip)
    q = p[sisa] / p[sisa].sum()
    il = rng.choice(sisa, size=min(kl, len(sisa)), replace=False, p=q)
    st.session_state["rt_ms_putus"] = sorted(int(i) for i in ip)
    st.session_state["rt_ms_lambat"] = sorted(int(i) for i in il)


def _cb_reset():
    st.session_state["rt_ms_putus"] = []
    st.session_state["rt_ms_lambat"] = []


# ------------------------------------------------------------------ peta
def _dasar(gdf):
    b = gdf.total_bounds
    m = folium.Map(tiles="OpenStreetMap", control_scale=True)
    m.fit_bounds([[b[1], b[0]], [b[3], b[2]]])
    return m


def _peta_gangguan(gdf, nama, P, L):
    m = _dasar(gdf)
    d = gdf[["geometry"]].copy()
    d["idx"] = d.index
    d["nama"] = nama.values
    for judul, mask, warna, w, dash in (
        ("Normal", ~d.index.isin(P | L), "#607d8b", 2, None),
        ("Terganggu (lebih lambat)", d.index.isin(L), "#ef6c00", 5, None),
        ("Putus", d.index.isin(P), "#c62828", 6, "8 6"),
    ):
        sub = d[mask]
        if sub.empty:
            continue
        folium.GeoJson(
            sub, name=judul,
            style_function=lambda _, c=warna, w=w, ds=dash: {"color": c, "weight": w, "opacity": .95, "dashArray": ds},
            tooltip=folium.GeoJsonTooltip(fields=["idx", "nama"], aliases=["No.", "Ruas"]),
        ).add_to(m)
    Draw(export=False, position="topleft", edit_options={"edit": False},
         draw_options={"polygon": True, "rectangle": True, "polyline": False, "circle": False,
                       "marker": False, "circlemarker": False}).add_to(m)
    return m


def _titik_ke_peta(m, tp):
    for _, r in tp.iterrows():
        folium.Marker([r["lat"], r["lon"]], tooltip=f"{r['jenis']}: {r['nama']}",
                      icon=folium.Icon(color=WARNA_TITIK.get(r["jenis"], "gray"), icon="info-sign")).add_to(m)


def _garis(m, gdf, idx, warna, w, ket, dash=None, op=.9):
    for i in idx:
        folium.GeoJson(gdf.geometry.iloc[i].__geo_interface__, tooltip=ket,
                       style_function=lambda _, c=warna, w=w, ds=dash, o=op:
                       {"color": c, "weight": w, "opacity": o, "dashArray": ds}).add_to(m)


# ------------------------------------------------------------------ data titik & perjalanan
def _baca_titik(f, jenis, kn, kb):
    nm = f.name.lower()
    if nm.endswith((".csv", ".xlsx")):
        t = pd.read_excel(f) if nm.endswith("xlsx") else pd.read_csv(f, sep=None, engine="python")
        t = t.rename(columns=lambda c: str(c).lower().strip())
        for c in TITIK_COLS:
            if c not in t.columns:
                t[c] = {"nama": "", "bobot": 1.0}.get(c, np.nan)
        return t[TITIK_COLS]
    d = _BACA(f.name, f.getvalue())
    d = (d.set_crs(4326) if d.crs is None else d).to_crs(4326)
    p = d.geometry.representative_point()
    out = pd.DataFrame({
        "nama": d[kn].astype(str).values if kn != "(tanpa nama)" else [f"{jenis}-{i + 1}" for i in range(len(d))],
        "jenis": d[jenis].astype(str).str.lower().values if (jenis in d.columns and jenis not in rt.JENIS_TITIK) else jenis,
        "lat": p.y.values, "lon": p.x.values,
        "bobot": sc.to_num(d[kb]).fillna(1.0).values if kb != "(sama rata)" else 1.0})
    return out


def _titik_contoh(G):
    rng = np.random.default_rng(3)
    nodes = G.graph["nodes"]
    pilih = rng.choice(len(nodes), size=min(12, len(nodes)), replace=False)
    peran = ["rs", "rs", "posko", "depot"] + ["permukiman"] * 8
    rows = []
    for k, (j, p) in enumerate(zip(pilih, peran)):
        lon, lat = nodes[j]
        rows.append({"nama": f"{p.upper()} contoh {k + 1}", "jenis": p, "lat": lat, "lon": lon, "bobot": 1.0})
    return pd.DataFrame(rows, columns=TITIK_COLS)


def _od_dari_geo(f):
    d = _BACA(f.name, f.getvalue())
    d = (d.set_crs(4326) if d.crs is None else d).to_crs(4326)
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


# ------------------------------------------------------------------ tab utama
def tab(gdf, hasil, kelas, hz, baca_geo):
    """gdf: EPSG:4326, indeks 0..n-1. hasil: keluaran scoring + kolom 'ruas'. kelas: arteri/kolektor/lokal/lainnya."""
    global _BACA
    _BACA = baca_geo
    n = len(gdf)
    nama = hasil["ruas"].astype(str).reset_index(drop=True)
    sig = f"{n}-{[round(float(x), 5) for x in gdf.total_bounds]}"

    S = st.session_state
    if S.get("rt_sig") != sig:
        for k in [k for k in S if k.startswith("rt_")]:
            del S[k]
        S["rt_sig"] = sig
        S["rt_utm"] = gdf.to_crs(gdf.estimate_utm_crs())
        S["rt_ms_putus"], S["rt_ms_lambat"] = [], []
        S["rt_titik"], S["rt_ver"] = pd.DataFrame(columns=TITIK_COLS), 0
    S["rt_hz"] = hz

    # ---------------- 1. bencana & parameter
    st.subheader("Simulasi gangguan dan urutan perbaikan")
    c1, c2 = st.columns([1, 2])
    bencana = c1.radio("Jenis bencana", list(JENIS), format_func=JENIS.get, key="rt_bencana")
    c2.info(CATATAN[bencana])

    with st.expander("Bobot prioritas, kecepatan, dan durasi perbaikan (asumsi awal, bisa diubah)"):
        st.caption("Bobot dinormalkan otomatis. Angka awal adalah asumsi berdasarkan literatur, "
                   "belum dikalibrasi; sesuaikan dengan data BPBD/PU setempat.")
        cols = st.columns(6)
        w = {k: cols[j].slider(LABEL_METRIK[k], 0.0, 1.0, float(rt.PROFIL[bencana][k]), 0.05,
                               key=f"rt_w_{bencana}_{k}") for j, k in enumerate(LABEL_METRIK)}
        a, b, c, d = st.columns(4)
        kec = {k: a.number_input(f"Kecepatan {k} (km/jam)", 5.0, 120.0, v, 5.0, key=f"rt_v_{k}")
               for k, v in rt.KECEPATAN.items()}
        hamb = b.slider("Kecepatan ruas terganggu (× normal)", 0.05, 1.0, rt.HAMBATAN[bencana], 0.05,
                        key=f"rt_h_{bencana}")
        tetap = c.number_input("Durasi tetap per ruas (jam)", 0.1, 200.0, rt.PERBAIKAN[bencana][0], 0.5,
                               key=f"rt_dt_{bencana}")
        perkm = d.number_input("Tambahan durasi per km (jam)", 0.0, 200.0, rt.PERBAIKAN[bencana][1], 1.0,
                               key=f"rt_dk_{bencana}")
        e, f_, g_ = st.columns(3)
        perlu = e.checkbox("Ruas putus hanya bisa dikerjakan jika alat berat bisa sampai",
                           rt.PERLU_DEPOT[bencana], key=f"rt_pd_{bencana}")
        pen = f_.number_input("Penalti perjalanan terputus (menit)", 10, 1440, 120, 10, key="rt_pen")
        des = g_.selectbox("Penyatuan ujung ruas", [5, 4], format_func=lambda x: f"{x} desimal (~{1 if x == 5 else 11} m)",
                           key="rt_des")

    G = _graf(gdf, tuple(kelas), tuple(sorted(kec.items())), sig, des)
    ncomp = nx.number_connected_components(G)
    besar = max(len(c) for c in nx.connected_components(G)) / G.number_of_nodes()
    st.caption(f"Graf: {G.number_of_nodes()} simpul, {len({d['i'] for *_, d in G.edges(data=True)})} ruas, "
               f"{ncomp} komponen terhubung (terbesar {besar:.0%}).")
    if besar < 0.9:
        st.warning("Jaringan terpecah menjadi banyak komponen: kemungkinan ruas bersilangan di tengah garis "
                   "belum dipecah di persimpangan. Coba 'Penyatuan ujung ruas' 4 desimal, atau pecah ruas di "
                   "QGIS (Split with lines / Intersections) sebelum diunggah.")

    t1, t2, t3, t4 = st.tabs(["1. Gangguan", "2. Titik & perjalanan", "3. Urutan perbaikan", "4. Waktu tempuh"])

    # ---------------- tab 1: gangguan
    with t1:
        P, L = set(S["rt_ms_putus"]), set(S["rt_ms_lambat"]) - set(S["rt_ms_putus"])
        kiri, kanan = st.columns([3, 2])
        with kiri:
            out = st_folium(_peta_gangguan(gdf, nama, P, L), height=540, use_container_width=True,
                            returned_objects=["last_object_clicked", "all_drawings"], key="rt_map1")
            S["rt_draw"] = (out or {}).get("all_drawings") or []
            S["rt_sel"] = _terdekat((out or {}).get("last_object_clicked"))
            st.markdown("<span style='color:#607d8b'>■</span> normal &nbsp; <span style='color:#ef6c00'>■</span> terganggu "
                        "&nbsp; <span style='color:#c62828'>■</span> putus", unsafe_allow_html=True)
        with kanan:
            m1, m2, m3 = st.columns(3)
            m1.metric("Putus", len(P))
            m2.metric("Terganggu", len(L))
            m3.metric("Panjang putus (km)", f"{sum(G.graph['panjang'][i] for i in P) / 1000:.1f}")

            sel = S.get("rt_sel")
            st.markdown("**Ruas yang diklik di peta**")
            st.write(f"{sel}: {nama[sel]}" if sel is not None else "Klik sebuah ruas di peta.")
            b1, b2, b3 = st.columns(3)
            b1.button("Putus", on_click=_cb_klik, args=("putus",), disabled=sel is None, key="rt_b_p")
            b2.button("Terganggu", on_click=_cb_klik, args=("lambat",), disabled=sel is None, key="rt_b_l")
            b3.button("Normal", on_click=_cb_klik, args=("normal",), disabled=sel is None, key="rt_b_n")

            st.markdown(f"**Area gambar ({len(S['rt_draw'])})**")
            st.caption("Gambar poligon/persegi di peta (ikon di kiri atas), mis. area genangan atau longsoran.")
            a1, a2, a3 = st.columns(3)
            a1.button("→ Putus", on_click=_cb_area, args=("putus",), disabled=not S["rt_draw"], key="rt_a_p")
            a2.button("→ Terganggu", on_click=_cb_area, args=("lambat",), disabled=not S["rt_draw"], key="rt_a_l")
            a3.button("→ Normal", on_click=_cb_area, args=("normal",), disabled=not S["rt_draw"], key="rt_a_n")

            with st.expander("Unggah GeoJSON area/garis gangguan"):
                st.file_uploader("Poligon genangan, garis, atau titik", type=["geojson", "json", "gpkg", "zip"],
                                 key="rt_up_gang")
                st.caption("Kolom `status` (putus / terganggu) dibaca bila ada.")
                u1, u2 = st.columns(2)
                dflt = u1.selectbox("Status bawaan", ["putus", "lambat"],
                                    format_func={"putus": "Putus", "lambat": "Terganggu"}.get, key="rt_up_st")
                buf = u2.number_input("Buffer titik/garis (m)", 0, 500, 30, 10, key="rt_up_buf")
                st.button("Terapkan ke ruas", on_click=_cb_unggah, args=(dflt, buf),
                          disabled=S.get("rt_up_gang") is None, key="rt_up_btn")

            with st.expander("Skenario acak berdasarkan bahaya"):
                pil = list(hz)
                cocok = next((j for j, k in enumerate(pil) if bencana in str(k).lower()), 0)
                st.selectbox("Acuan bahaya", pil, index=cocok, key="rt_hz_pilih")
                r1, r2, r3 = st.columns(3)
                r1.number_input("Ruas putus", 0, n, min(5, n), key="rt_n_putus")
                r2.number_input("Ruas terganggu", 0, n, min(8, n), key="rt_n_lambat")
                r3.number_input("Seed", 0, 9999, 1, key="rt_seed")
                st.button("Buat skenario acak", on_click=_cb_acak, key="rt_acak")
            st.button("Hapus semua gangguan", on_click=_cb_reset, key="rt_reset")

            st.multiselect("Ruas putus", range(n), key="rt_ms_putus", format_func=lambda i: f"{i}: {nama[i]}")
            st.multiselect("Ruas terganggu", range(n), key="rt_ms_lambat", format_func=lambda i: f"{i}: {nama[i]}")

    P = set(S["rt_ms_putus"])
    L = set(S["rt_ms_lambat"]) - P
    ada = {d["i"] for *_, d in G.edges(data=True)}
    P &= ada
    L &= ada

    # ---------------- tab 2: titik & perjalanan
    with t2:
        with st.expander("Unggah titik (RS, posko, depot alat berat, permukiman)"):
            fs = st.file_uploader("GeoJSON / GeoPackage / Shapefile zip / CSV / Excel", accept_multiple_files=True,
                                  type=["geojson", "json", "gpkg", "zip", "csv", "xlsx"], key="rt_up_titik")
            st.caption("Tabel CSV/Excel: kolom `nama, jenis, lat, lon, bobot` (jenis: rs, posko, depot, permukiman). "
                       "Bobot permukiman = jumlah penduduk/rumah.")
            opsi = []
            for f in fs or []:
                if f.name.lower().endswith((".csv", ".xlsx")):
                    opsi.append((f, None, None, None))
                    continue
                dd = _BACA(f.name, f.getvalue())
                kk = [c for c in dd.columns if c != "geometry"]
                x1, x2, x3 = st.columns(3)
                j = x1.selectbox(f"Jenis titik: {f.name}", list(rt.JENIS_TITIK) + kk, key=f"rt_j_{f.name}",
                                 help="Pilih jenis tetap, atau nama kolom yang berisi jenis.")
                kn = x2.selectbox("Kolom nama", ["(tanpa nama)"] + kk, key=f"rt_kn_{f.name}")
                kb = x3.selectbox("Kolom bobot", ["(sama rata)"] + kk, key=f"rt_kb_{f.name}")
                opsi.append((f, j, kn, kb))
            if st.button("Tambahkan ke daftar titik", disabled=not opsi, key="rt_add_titik"):
                bag = [S["rt_titik"]] + [_baca_titik(f, j, kn, kb) for f, j, kn, kb in opsi]
                S["rt_titik"] = pd.concat(bag, ignore_index=True)
                S["rt_ver"] += 1
                st.rerun()
        x1, x2 = st.columns(2)
        if x1.button("Isi titik contoh (acak di jaringan)", key="rt_contoh"):
            S["rt_titik"] = _titik_contoh(G)
            S["rt_ver"] += 1
            st.rerun()
        if x2.button("Kosongkan titik", key="rt_kosong"):
            S["rt_titik"] = pd.DataFrame(columns=TITIK_COLS)
            S["rt_ver"] += 1
            st.rerun()

        titik = st.data_editor(
            S["rt_titik"], num_rows="dynamic", hide_index=True, width="stretch", key=f"rt_ed_{S['rt_ver']}",
            column_config={"jenis": st.column_config.SelectboxColumn(options=list(rt.JENIS_TITIK)),
                           "lat": st.column_config.NumberColumn(format="%.6f"),
                           "lon": st.column_config.NumberColumn(format="%.6f")})
        tp = rt.pasang_titik(G, titik)
        if len(tp) and tp["jarak_snap_m"].max() > 500:
            st.warning(f"{int((tp['jarak_snap_m'] > 500).sum())} titik lebih dari 500 m dari jalan terdekat; "
                       "periksa koordinat (lat/lon tidak tertukar?).")
        rs_n = set(tp.loc[tp.jenis == "rs", "node"])
        posko_n = set(tp.loc[tp.jenis == "posko", "node"])
        depot_n = set(tp.loc[tp.jenis == "depot", "node"])
        perm = tp[tp.jenis == "permukiman"]
        pop = {}
        for nd, bw in zip(perm["node"], perm["bobot"]):
            pop[nd] = pop.get(nd, 0.0) + float(bw)
        rs_nama = {nd: nm for nd, nm in zip(tp.loc[tp.jenis == "rs", "node"], tp.loc[tp.jenis == "rs", "nama"])}

        st.markdown("**Perjalanan (asal → tujuan) untuk membandingkan waktu tempuh**")
        mode = st.radio("Sumber", ["Otomatis: permukiman → RS terdekat", "Unggah data perjalanan"],
                        horizontal=True, key="rt_modeod")
        maks = st.slider("Maksimum perjalanan", 5, 60, 20, key="rt_maxod")
        od, obs = [], []
        if mode.startswith("Otomatis"):
            if rs_n and len(perm):
                perm_list = [(r["nama"], r["node"], r["bobot"]) for _, r in perm.iterrows()]
                od = rt.od_otomatis(G, perm_list, list(rs_n), rs_nama, maks)
                obs = [np.nan] * len(od)
            else:
                st.info("Butuh minimal 1 titik `rs` dan 1 titik `permukiman`.")
        else:
            fo = st.file_uploader("Tabel (CSV/Excel) atau GeoJSON garis perjalanan", key="rt_up_od",
                                  type=["csv", "xlsx", "geojson", "json", "gpkg", "zip"])
            st.caption("Kolom: `nama, asal_lat, asal_lon, tujuan_lat, tujuan_lon, waktu_menit (opsional, hasil "
                       "pengukuran/Google Maps), bobot (opsional)`. GeoJSON: tiap garis = satu perjalanan "
                       "(ujung awal dan akhir).")
            if fo is not None:
                nm = fo.name.lower()
                if nm.endswith((".csv", ".xlsx")):
                    tb = pd.read_excel(fo) if nm.endswith("xlsx") else pd.read_csv(fo, sep=None, engine="python")
                else:
                    tb = _od_dari_geo(fo)
                try:
                    od, obs = rt.od_dari_tabel(G, tb.head(maks))
                except ValueError as e:
                    st.error(str(e))

        pm = _dasar(gdf)
        _garis(pm, gdf, range(n), "#90a4ae", 2, "ruas", op=.7)
        _garis(pm, gdf, sorted(P), "#c62828", 5, "putus", "8 6")
        _titik_ke_peta(pm, tp)
        for nm_, o, t_, _ in od:
            folium.PolyLine([(o[1], o[0]), (t_[1], t_[0])], color="#6a1b9a", weight=1.5, opacity=.6,
                            dash_array="4 4", tooltip=nm_).add_to(pm)
        st_folium(pm, height=420, use_container_width=True, returned_objects=[], key="rt_map2")

    # ---------------- tab 3: urutan perbaikan
    with t3:
        if not P:
            st.info("Tandai minimal satu ruas putus di tab 1.")
        else:
            km = {i: G.graph["panjang"][i] / 1000 for i in P}
            dfd = pd.DataFrame({"ruas": sorted(P), "nama": [nama[i] for i in sorted(P)],
                                "panjang_km": [km[i] for i in sorted(P)],
                                "durasi_jam": [tetap + perkm * km[i] for i in sorted(P)]})
            with st.expander(f"Durasi perbaikan tiap ruas putus ({len(P)} ruas, bisa diedit)"):
                dfe = st.data_editor(dfd, hide_index=True, disabled=["ruas", "nama", "panjang_km"],
                                     width="stretch", key=f"rt_dur_{_md5(sorted(P), bencana, tetap, perkm)}",
                                     column_config={"panjang_km": st.column_config.NumberColumn(format="%.2f"),
                                                    "durasi_jam": st.column_config.NumberColumn(min_value=0.05,
                                                                                                 format="%.1f")})
            dur = dict(zip(dfe["ruas"], dfe["durasi_jam"]))

            opsi_algo = ["greedy", "krus", "sa", "acak"] + (["optimal"] if len(P) <= 14 else [])
            a1, a2, a3, a4 = st.columns(4)
            algos = a1.multiselect("Algoritma yang dibandingkan", opsi_algo, default=["greedy", "krus", "sa"],
                                   format_func=NAMA_ALGO.get, key="rt_algos",
                                   help="Optimal tepat hanya untuk ≤14 ruas putus dan bisa lambat.")
            kru = a2.number_input("Jumlah tim kerja", 1, 20, 1, key="rt_kru")
            it = a3.number_input("Iterasi SA", 50, 5000, 400, 50, key="rt_it")
            dt = a4.number_input("Batas waktu SA (detik)", 5, 300, 20, 5, key="rt_dts")

            sig_run = _md5(sorted(P), sorted(L), bencana, sorted(w.items()), sorted(dur.items()), hamb, perlu, pen,
                           titik.to_csv(index=False), [(o[0], o[1], o[2], o[3]) for o in od], kru, algos,
                           sorted(kec.items()), des)
            S["rt_sigrun"] = sig_run

            if st.button("Jalankan simulasi", type="primary", disabled=not algos, key="rt_run"):
                if len(P) < len(S["rt_ms_putus"]):
                    st.warning("Sebagian ruas putus tidak terbaca sebagai garis di graf dan diabaikan.")
                if not (rs_n or posko_n or depot_n or od):
                    st.warning("Belum ada titik RS/posko/depot/perjalanan: urutan hanya memakai keterhubungan dan "
                               "krusialitas ruas.")
                lo, hi = float(hasil["skor"].min()), float(hasil["skor"].max())
                krus = ((hasil["skor"] - lo) / ((hi - lo) or 1.0)).to_dict()
                M = rt.Model(G, P, {i: hamb for i in L}, rs=rs_n, posko=posko_n, depot=depot_n, pop=pop, od=od,
                             bobot=w, perlu_depot=perlu, penalti=pen * 60.0, dur=dur, krus=krus)
                urut, jad, tit, rg = {}, {}, {}, {}
                bar = st.progress(0.0, text="Menyusun urutan...")
                import random
                for j, a in enumerate(algos):
                    bar.progress(j / len(algos), text=f"{NAMA_ALGO[a]}...")
                    if a == "acak":
                        acak = sorted(P)
                        random.Random(7).shuffle(acak)
                        u = rt.dekode(M, acak)
                    else:
                        u = rt.susun(M, a, int(it), float(dt))
                    urut[a] = u
                    jad[a], tit[a] = rt.jadwal(M, u, int(kru))
                    rg[a] = rt.ringkas(tit[a])
                bar.empty()
                S["rt_res"] = {"sig": sig_run, "M": M, "urut": urut, "jad": jad, "tit": tit, "rg": rg,
                               "obs": obs, "algos": list(algos), "kru": int(kru)}

            res = S.get("rt_res")
            if res and res["sig"] != sig_run:
                st.info("Pengaturan berubah sejak simulasi terakhir. Klik **Jalankan simulasi** untuk memperbarui.")
            elif res:
                ringkas = pd.DataFrame(res["rg"]).T.loc[res["algos"]]
                ringkas.insert(0, "algoritma", [NAMA_ALGO[a] for a in ringkas.index])
                ref = ringkas["luas"].max() or 1.0
                ringkas["luas_vs_terburuk_pct"] = 100 * ringkas["luas"] / ref
                st.dataframe(ringkas.rename(columns={"selesai": "selesai_jam", "t50": "50%_pulih_jam",
                                                     "t90": "90%_pulih_jam", "luas": "luas_kerugian"}),
                             hide_index=True, width="stretch",
                             column_config={k: st.column_config.NumberColumn(format="%.1f") for k in
                                            ["selesai_jam", "50%_pulih_jam", "90%_pulih_jam", "luas_kerugian",
                                             "luas_vs_terburuk_pct"]})
                st.caption("Luas kerugian = luas di bawah kurva (1 − pemulihan) terhadap waktu; makin kecil makin baik.")
                tmax = max(r["selesai"] for r in res["rg"].values()) or 1.0
                grid = np.linspace(0, tmax, 120)
                st.line_chart(pd.DataFrame({NAMA_ALGO[a]: rt.kurva_grid(res["tit"][a], grid) for a in res["algos"]},
                                           index=pd.Index(grid, name="jam sejak bencana")),
                              y_label="Pemulihan (%)")

                a = st.selectbox("Tampilkan urutan", res["algos"], format_func=NAMA_ALGO.get, key="rt_show")
                jd = res["jad"][a].copy()
                jd.insert(2, "nama_ruas", [nama[i] for i in jd["ruas"]])
                st.dataframe(jd, hide_index=True, width="stretch", column_config={
                    c: st.column_config.NumberColumn(format="%.1f") for c in
                    ["mulai_jam", "selesai_jam", "durasi_jam", "mobilisasi_jam", "pulih_sebelum_pct",
                     "pulih_sesudah_pct", "rs_menit", "posko_menit"]})
                st.download_button("Unduh urutan (CSV)", jd.to_csv(index=False).encode("utf-8"),
                                   f"urutan_perbaikan_{a}.csv", "text/csv")

                pm = _dasar(gdf)
                _garis(pm, gdf, range(n), "#90a4ae", 2, "ruas", op=.6)
                _garis(pm, gdf, sorted(L), "#ef6c00", 4, "terganggu")
                cm = bcm.LinearColormap(["#b71c1c", "#ff9800", "#fdd835", "#7cb342"], vmin=1, vmax=max(len(jd), 2),
                                        caption="Urutan perbaikan (1 = pertama)")
                for _, r in jd.iterrows():
                    _garis(pm, gdf, [int(r["ruas"])], cm(r["urutan"]), 7,
                           f"#{int(r['urutan'])} {r['nama_ruas']}: jam {r['mulai_jam']:.1f}–{r['selesai_jam']:.1f}")
                cm.add_to(pm)
                _titik_ke_peta(pm, tp)
                st_folium(pm, height=480, use_container_width=True, returned_objects=[], key="rt_map3")

    # ---------------- tab 4: waktu tempuh
    with t4:
        res = S.get("rt_res")
        if not P or not res or res["sig"] != S.get("rt_sigrun"):
            st.info("Jalankan simulasi di tab 3 terlebih dahulu.")
        elif not res["M"].od:
            st.info("Belum ada data perjalanan. Isi titik RS + permukiman, atau unggah data perjalanan di tab 2.")
        else:
            M = res["M"]
            a = st.selectbox("Urutan perbaikan yang dipakai", res["algos"], format_func=NAMA_ALGO.get, key="rt_a4")
            jd = res["jad"][a].sort_values("selesai_jam")
            tmax = float(jd["selesai_jam"].max())
            t = st.slider("Waktu sejak bencana (jam)", 0.0, tmax, 0.0, 0.5, key="rt_t4")
            rep = frozenset(int(i) for i in jd.loc[jd["selesai_jam"] <= t + 1e-9, "ruas"])
            st.caption(f"{len(rep)} dari {len(P)} ruas putus sudah diperbaiki pada jam ke-{t:.1f}.")

            tbl, jalur = rt.bandingkan_od(M, rep)
            tbl["observasi_menit"] = res["obs"]
            tbl["tambahan_menit"] = tbl["pulih_menit"] - tbl["normal_menit"]
            for c_ in ("terdampak_menit", "pulih_menit"):
                tbl[c_ + "_x"] = tbl[c_] / tbl["normal_menit"]
            mk = st.columns(4)
            mk[0].metric("Rata-rata normal (menit)", f"{tbl['normal_menit'].mean():.1f}")
            mk[1].metric("Rata-rata terdampak", f"{np.nanmean(tbl['terdampak_menit']):.1f}" if tbl["terdampak_menit"].notna().any() else "-")
            mk[2].metric("Rata-rata sekarang", f"{np.nanmean(tbl['pulih_menit']):.1f}" if tbl["pulih_menit"].notna().any() else "-")
            mk[3].metric("Perjalanan terputus", int(tbl["pulih_menit"].isna().sum()))
            ob = tbl.dropna(subset=["observasi_menit"])
            if len(ob):
                fk = float((ob["observasi_menit"] / ob["normal_menit"]).median())
                st.caption(f"Pembanding data observasi: median waktu observasi/model normal = {fk:.2f}. "
                           "Jauh dari 1 berarti kecepatan asumsi perlu dikalibrasi.")
            st.dataframe(tbl[["perjalanan", "normal_menit", "terdampak_menit", "pulih_menit", "tambahan_menit",
                              "observasi_menit"]], hide_index=True, width="stretch",
                         column_config={c_: st.column_config.NumberColumn(format="%.1f") for c_ in
                                        ["normal_menit", "terdampak_menit", "pulih_menit", "tambahan_menit",
                                         "observasi_menit"]})

            pilih = st.selectbox("Perjalanan ditampilkan rutenya", range(len(M.od)),
                                 format_func=lambda k: M.od[k][0], key="rt_pil4")
            pm = _dasar(gdf)
            _garis(pm, gdf, range(n), "#cfd8dc", 2, "ruas", op=.8)
            _garis(pm, gdf, sorted(P - rep), "#c62828", 5, "masih putus", "8 6")
            _garis(pm, gdf, sorted(rep), "#2e7d32", 4, "sudah diperbaiki", "2 6")
            for k, (nm_, o, t_, _) in enumerate(M.od):
                r = tbl.iloc[k]
                x = r["pulih_menit"] / r["normal_menit"] if pd.notna(r["pulih_menit"]) else np.nan
                warna = "#000000" if np.isnan(x) else "#2e9d57" if x <= 1.1 else "#e0a800" if x <= 1.5 else \
                    "#ef6c00" if x <= 2.5 else "#c62828"
                ket = f"{nm_}: normal {r['normal_menit']:.1f}, sekarang " + \
                      ("terputus" if np.isnan(x) else f"{r['pulih_menit']:.1f} menit")
                folium.PolyLine([(o[1], o[0]), (t_[1], t_[0])], color=warna, weight=3, opacity=.85,
                                tooltip=ket).add_to(pm)
            j = jalur[pilih]
            _garis(pm, gdf, j["normal"], "#1565c0", 9, "rute normal", op=.35)
            _garis(pm, gdf, j["terdampak"], "#6a1b9a", 5, "rute terdampak")
            _garis(pm, gdf, j["pulih"], "#00c853", 3, "rute sekarang")
            _titik_ke_peta(pm, tp)
            st_folium(pm, height=500, use_container_width=True, returned_objects=[], key="rt_map4")
            st.markdown("Garis lurus asal→tujuan: <span style='color:#2e9d57'>■</span> ≤1,1× normal &nbsp; "
                        "<span style='color:#e0a800'>■</span> ≤1,5× &nbsp; <span style='color:#ef6c00'>■</span> ≤2,5× &nbsp; "
                        "<span style='color:#c62828'>■</span> &gt;2,5× &nbsp; <span style='color:#000'>■</span> terputus. "
                        "Rute: biru = normal, ungu = terdampak, hijau = sekarang.", unsafe_allow_html=True)

            pts = [(0.0, frozenset())] + [(float(s), frozenset(int(i) for i in jd.loc[jd["selesai_jam"] <= s + 1e-9, "ruas"]))
                                          for s in jd["selesai_jam"]]
            kur = []
            for s, rp in pts:
                mt = M.hitung(rp)["mentah"]
                kur.append({"jam": s, "Waktu tempuh rata-rata (menit)": mt.get("od", np.nan) / 60,
                            "Waktu ke RS (menit)": mt.get("rs", np.nan) / 60})
            st.markdown("**Waktu tempuh terhadap kemajuan perbaikan**")
            st.line_chart(pd.DataFrame(kur).set_index("jam").dropna(axis=1, how="all"))

    with st.expander("Dasar literatur dan batasan model"):
        st.markdown("""
- Prioritas akses ke fasilitas kritis (RS) setelah banjir: Gorani & Dong, *Optimization of Equitable Routes to
  Critical Facilities After Flood Events* (Univ. Virginia / Univ. Delaware).
- Penjadwalan perbaikan jalan dengan metode heuristik: Moghtadernejad, Adey & Hackl (2022),
  *Determination of Postdisaster Restoration Programs for Road Networks Using a Double-Stage Optimization Approach*,
  J. Infrastructure Systems, doi:10.1061/(ASCE)IS.1943-555X.0000700 (SA dan PSO efisien pada kasusnya).
- Heuristik bisa kurang optimal bila sumber daya sangat terbatas: *Long-term scheduling for road network disaster
  recovery*, Int. J. Disaster Risk Reduction 42 (2020) 101353.
- Akses ke fasilitas vital (RS, kepolisian, pemadam) sebagai tujuan pemulihan: *Methodology for planning
  reconstruction activities after a disaster considering interdependencies and priorities* (Steiner tree + penjadwalan).
- Konsep jaringan layanan darurat di atas jaringan jalan: *Restoration of services in disrupted infrastructure
  systems: A network science approach* (PMC5812613).

**Batasan:** angka bobot per bencana, kecepatan, dan durasi adalah **asumsi** yang menerjemahkan temuan di atas;
bukan angka dari jurnal. Untuk longsor (akses alat berat), dukungan yang ditemukan baru berupa praktik lapangan
penanganan bencana, belum jurnal yang memberi bobot. Waktu tempuh dihitung pada graf tanpa kemacetan dan tanpa
kapasitas jalan. Ruas yang bersilangan di tengah garis hanya terhubung jika dipecah di persimpangan.
""")
