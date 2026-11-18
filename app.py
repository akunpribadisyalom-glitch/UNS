import folium
import networkx as nx
import numpy as np
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from shapely.geometry import shape
from streamlit_folium import st_folium

import animasi as an
import gaya
import ingest
import nama_jalan as nj
import pemulihan as pm
import peta
import restorasi as rt
import scoring as sc

st.set_page_config(page_title="Jalan Siaga", page_icon="🛣️", layout="wide", initial_sidebar_state="collapsed")
gaya.terapkan()
S = st.session_state

bencana = "banjir"        # aplikasi ini khusus banjir; bahaya hanya dari probabilitas banjir (fl_max)
MODE = {"putus": "🔴 Putus", "lambat": "🟠 Terganggu", "normal": "✅ Normal", "rs": "🏥 RS", "posko": "⛺ Posko",
        "depot": "🚜 Alat berat", "quarry": "⛏️ Quarry", "permukiman": "🏠 Permukiman"}
STATUS_MODE = ("putus", "lambat", "normal")
TC = ingest.TITIK_COLS
JENIS_LBL = {"rs": "🏥 RS", "posko": "⛺ posko", "depot": "🚜 alat berat", "quarry": "⛏️ quarry",
             "permukiman": "🏠 permukiman"}

st.markdown("<div class='hero'><h1>🛣️ Jalan Siaga</h1>",
            unsafe_allow_html=True)


def set_status(idx, status):
    P, L = set(S["rt_putus"]), set(S["rt_lambat"])
    idx = {int(i) for i in idx}
    P -= idx
    L -= idx
    if status == "putus":
        P |= idx
    elif status == "lambat":
        L |= idx
    S["rt_putus"], S["rt_lambat"] = P, L


# =============================================================== 1. UNGGAH
with st.container(key="kartu_unggah"):
    st.markdown("### 1️⃣ Unggah")
    fs = st.file_uploader(
        "Seret semua file sekaligus. Jalan wajib; RS, posko, alat berat, quarry, permukiman, area banjir/longsor, "
        "dan data perjalanan boleh menyusul. Perannya dibaca otomatis.",
        accept_multiple_files=True, type=["geojson", "json", "gpkg", "zip", "csv", "xlsx"], key="up")
    if st.button("🎈 Coba dengan data contoh", key="btn_demo"):
        S["demo"] = True
    demo = bool(S.get("demo")) and not fs

    items = []
    for f in fs or []:
        try:
            tebak, obj = ingest.baca_berkas(f, S.get("rt_epsg", 32749))
        except Exception as e:
            st.error(f"{f.name}: tidak bisa dibaca ({e})")
            continue
        pilih = S.get(f"rt_peran_{f.name}", "otomatis")
        items.append((f.name, tebak if pilih == "otomatis" else pilih, obj, tebak))

    if demo:
        g0, t0, p0 = ingest.contoh()
        R = {"jalan": g0, "titik": t0, "gangguan": [], "od": None, "catatan": []}
    else:
        R = ingest.rakit([(n_, p_, o_) for n_, p_, o_, _ in items])

    if R["jalan"] is None:
        for c_ in R["catatan"]:
            st.warning(c_)
        st.markdown("<div class='kosong'><span class='em'>🗺️</span>Belum ada jalan. Seret file jalan "
                    "(GeoJSON, GeoPackage, atau Shapefile .zip) ke kotak di atas.<br>Peta akan langsung membuka "
                    "lokasi data Anda, di mana pun itu.</div>", unsafe_allow_html=True)
        st.stop()

    gdf = R["jalan"]
    gdf = gdf[gdf.geometry.geom_type.str.contains("Line")].reset_index(drop=True)
    if gdf.empty:
        st.error("File jalan tidak berisi garis. Pastikan layer jalan (bukan titik atau poligon) yang diunggah.")
        st.stop()

    chips = [gaya.chip(f"🛣️ {len(gdf)} ruas", "ok")]
    if R["titik"] is not None and len(R["titik"]):
        for j, c_ in R["titik"]["jenis"].value_counts().items():
            chips.append(gaya.chip(f"{JENIS_LBL.get(j, j)} × {c_}", "ok"))
    if R["gangguan"]:
        chips.append(gaya.chip(f"🌊 area gangguan × {len(R['gangguan'])}", "ok"))
    if R["od"] is not None:
        chips.append(gaya.chip(f"🚗 perjalanan × {len(R['od'])}", "ok"))
    chips += [gaya.chip(c_, "warn") for c_ in R["catatan"]]
    st.markdown("".join(chips), unsafe_allow_html=True)
    if demo:
        st.caption("Data contoh (lokasi fiktif). Peta otomatis membuka lokasi data yang Anda unggah nanti, di mana pun.")

    if R["gangguan"]:
        st.segmented_control("Area gangguan yang diunggah dianggap", ["putus", "lambat"],
                             format_func={"putus": "🔴 Putus", "lambat": "🟠 Terganggu"}.get, default="putus",
                             key="rt_gstat")
    if items:
        with st.expander("Salah baca? Ubah peran file"):
            for nm, _, _, tebak in items:
                st.selectbox(f"{nm} (terbaca: {tebak})", ingest.PERAN, key=f"rt_peran_{nm}")

# =============================================================== reset saat data berganti
sig_up = pm.md5([(f.name, f.size) for f in fs or []], demo, [p_ for _, p_, _, _ in items])
if S.get("rt_sig") != sig_up:
    for k in [k for k in S if k.startswith("rt_") and k != "rt_epsg" and not k.startswith("rt_peran_")]:
        del S[k]
    S["rt_sig"] = sig_up
    S["rt_utm"] = pm.utm_dari(gdf)
    S["rt_putus"], S["rt_lambat"] = (set(p0) if demo else set()), set()
    t_ = R["titik"] if R["titik"] is not None else pd.DataFrame(columns=TC + ["sumber"])
    S["rt_titik"] = t_.reindex(columns=TC + ["sumber"])
    S["rt_ver"], S["rt_mapver"], S["rt_view"], S["rt_klik"] = 0, 0, None, None
    S["rt_draw_done"], S["rt_res"] = set(), None

sig_g = pm.md5(sig_up, S.get("rt_gstat", "putus"))
if R["gangguan"] and S.get("rt_gsig") != sig_g:           # area gangguan dari file -> ruas putus/terganggu
    default = S.get("rt_gstat", "putus")
    for _, d in R["gangguan"]:
        kol_s = next((c for c in d.columns if str(c).lower() in ("status", "kondisi", "gangguan")), None)
        for _, r in d.iterrows():
            st_ = default
            if kol_s is not None:
                v = str(r[kol_s]).lower()
                st_ = "putus" if any(x in v for x in ("putus", "rusak", "tutup", "timbun")) else \
                    "lambat" if any(x in v for x in ("ganggu", "lambat", "genang", "banjir")) else default
            set_status(pm.kena(S["rt_utm"], [r.geometry]), st_)
    S["rt_gsig"] = sig_g

# =============================================================== tata letak (urutan tampil)
c_bencana = st.container(key="kartu_bencana")
c_main = st.container()
c_titik = st.expander("📍 Daftar titik (RS, posko, alat berat, quarry, permukiman)")
c_lain = st.expander("📊 Peringkat krusialitas, dasar ilmiah, dan unduhan")
c_adv = st.expander("⚙️ Pengaturan lanjutan (nilai awal sudah siap pakai)")

# =============================================================== pengaturan lanjutan
kol = [c for c in gdf.columns if c != "geometry"]
pil = lambda rol: [c for c in kol if ingest.tebak_kolom(c) == rol]
fl_awal = ingest.kolom_banjir(kol)
with c_adv:
    a1, a2, a3 = st.columns(3)
    fl = a1.selectbox("Kolom probabilitas banjir", ["(tidak ada)"] + kol, key="rt_fl",
                      index=(kol.index(fl_awal) + 1) if fl_awal is not None else 0,
                      help="Satu-satunya kolom bahaya yang dipakai. Bawaan: fl_max.")
    idc = a2.selectbox("Kolom nomor ruas", ["(nomor urut)"] + kol, key="rt_idc",
                       index=(kol.index(pil("id")[0]) + 1) if pil("id") else 0)
    kj = a3.selectbox("Kolom nama jalan di file (jika sudah ada)", ["(tidak ada)"] + kol, key="rt_kj",
                      index=(kol.index(pil("jalan")[0]) + 1) if pil("jalan") else 0)
    cari_on = st.checkbox("Cari nama jalan otomatis dari OpenStreetMap (butuh internet)", True, key="rt_cari")
    r1, r2 = st.columns(2)
    b_kuning = r1.number_input("Batas kuning (probabilitas banjir ≥)", 0.01, 0.99, sc.BATAS_AWAL[0], 0.01, key="rt_bk")
    b_merah = r2.number_input("Batas merah (probabilitas banjir ≥)", 0.01, 1.0, sc.BATAS_AWAL[1], 0.01, key="rt_bm")
    st.markdown("**Faktor tambahan (opsional).** Bawaan kosong, jadi skor hanya dari probabilitas banjir.")
    f1_, f2_, f3_ = st.columns(3)
    st_col = f1_.selectbox("Status jalan (arteri/kolektor/lokal)", ["(tidak ada)"] + kol, key="rt_stcol")
    konek = f2_.multiselect("Kolom konektivitas", kol, key="rt_konek")
    dens = f3_.multiselect("Kolom densitas permukiman", kol, key="rt_dens")
    s3, s4 = st.columns(2)
    inv = s3.checkbox("Konektivitas berupa jarak", False, key="rt_inv")
    mode_skor = s4.selectbox("Rumus skor bila ada faktor tambahan", ["mul", "add"], key="rt_mskor", format_func={
        "mul": "Bahaya × Konsekuensi", "add": "Jumlah terbobot"}.get)
    w1, w2, w3, w4 = st.columns(4)
    wh = w1.number_input("Bobot bahaya", 0, 10, 5, key="rt_wh")
    ws = w2.number_input("Bobot status jalan", 0, 10, 3, key="rt_ws")
    wk = w3.number_input("Bobot konektivitas", 0, 10, 4, key="rt_wk")
    wd = w4.number_input("Bobot densitas", 0, 10, 3, key="rt_wd")
    nilai = {"arteri": 1.0, "kolektor": 0.7, "lokal": 0.4, "lainnya": 0.2}
    p1, p2, p3, p4 = st.columns(4)
    kru = p1.number_input("Tim kerja", 1, 20, 1, key="rt_kru")
    tetap = p2.number_input("Durasi tetap/ruas (jam)", 0.1, 200.0, rt.PERBAIKAN[bencana][0], 0.5, key="rt_dt_banjir")
    perkm = p3.number_input("Tambahan per km (jam)", 0.0, 200.0, rt.PERBAIKAN[bencana][1], 1.0, key="rt_dk_banjir")
    p4.number_input("EPSG bila CRS file tak terbaca", 1000, 99999, 32749, key="rt_epsg")
    q1, q2 = st.columns(2)
    sa_on = q1.checkbox("Cari urutan lebih baik (simulated annealing, lebih lama)", False, key="rt_sa")
    opt_on = q2.checkbox("Hitung solusi optimal (hanya bila ≤14 ruas putus)", False, key="rt_opt")
    st.caption("Kecepatan jalan dan durasi perbaikan adalah asumsi awal; sesuaikan dengan data BPBD/PU setempat. "
               "Quarry dihitung sebagai sumber alat/material bersama alat berat.")

# =============================================================== 2. BANJIR + NAMA JALAN
with c_bencana:
    st.markdown("### 2️⃣ Banjir")
    st.markdown("Bahaya dibaca dari **probabilitas banjir (`fl_max`)** saja. Akses warga ke **rumah sakit** dan posko didahulukan.")
    if fl == "(tidak ada)":
        st.error("Kolom probabilitas banjir (fl_max) tidak ditemukan di file jalan, jadi semua ruas tampil hijau. "
                 "Pilih kolomnya di ⚙️ Pengaturan lanjutan. Kolom yang ada: " + ", ".join(map(str, kol)))
    ulang = st.button("🔄 Cari ulang nama jalan", key="rt_cari_ulang", disabled=not cari_on or demo)
    if cari_on and not demo and (ulang or (S.get("rt_nj") is None and S.get("rt_njgalat") is None)):
        bar = st.progress(0.0, text="Mengunduh nama jalan dari OpenStreetMap...")
        try:
            hasil_nj, cat_nj = nj.cari_nama(
                gdf, S["rt_utm"],
                progres=lambda i, n: bar.progress(min(i / max(n, 1), 1.0), text=f"Mengunduh peta OSM ({i + 1}/{n})..."))
            S["rt_nj"], S["rt_njcat"], S["rt_njgalat"] = hasil_nj, cat_nj, None
        except Exception as e:  # tanpa internet pun aplikasi tetap jalan, hanya tanpa nama jalan
            S["rt_nj"], S["rt_njcat"], S["rt_njgalat"] = None, None, str(e)
        bar.empty()
    if S.get("rt_njgalat"):
        st.warning(f"Nama jalan belum bisa dicari: {S['rt_njgalat']} Tabel tetap memakai nomor ruas.")
    elif S.get("rt_njcat"):
        st.caption(S["rt_njcat"] + " Pencocokan memakai jalan OSM terdekat (≤25 m), jadi sesekali bisa keliru di persimpangan.")

# =============================================================== skor (hanya probabilitas banjir, fl_max)
nol = pd.Series(0.0, index=gdf.index)
Hraw = sc.hazard(gdf, [fl], "max", None, False, True) if fl != "(tidak ada)" else nol
Sx = sc.status(gdf[st_col], nilai) if st_col != "(tidak ada)" else nol
K = sc.rata_minmax(gdf, konek, inv) if konek else nol
D = sc.rata_minmax(gdf, dens) if dens else nol
w = {"h": wh if fl != "(tidak ada)" else 0, "s": ws if st_col != "(tidak ada)" else 0, "k": wk if konek else 0,
     "d": wd if dens else 0}
hasil = sc.hitung(Hraw, Sx, K, D, w, mode_skor if fl != "(tidak ada)" else "add")
kelas_s = sc.kelas_banjir(Hraw, (b_kuning, b_merah))

# --- nomor ruas dan nama jalan
no_ruas = (gdf[idc].astype(str) if idc != "(nomor urut)" else pd.Series((gdf.index + 1).astype(str), index=gdf.index))
no_ruas = no_ruas.map(lambda x: f"Ruas {x}" if x.strip().isdigit() else x).reset_index(drop=True)
jalan = pd.Series("", index=gdf.index, dtype=object)
if kj != "(tidak ada)":
    jalan = gdf[kj].fillna("").astype(str).str.strip().replace({"nan": "", "None": ""})
yakin = pd.Series(np.nan, index=gdf.index)
if S.get("rt_nj") is not None:
    osm_n = S["rt_nj"]["nama_jalan"].reindex(gdf.index).fillna("")
    kosong = jalan == ""
    jalan = jalan.where(~kosong, osm_n)
    yakin = S["rt_nj"]["yakin_pct"].reindex(gdf.index).where(kosong)
jalan = jalan.reset_index(drop=True)
nama = no_ruas.where(jalan == "", no_ruas + " · " + jalan)

hasil.insert(0, "ruas", no_ruas)
hasil.insert(1, "nama_jalan", jalan)
hasil["prob_banjir"] = Hraw.values
hasil["kelas"] = kelas_s.values
hasil["yakin_nama_pct"] = yakin.values
g = gdf[["geometry"]].copy()
g["nama"], g["kelas"], g["skor"] = nama.values, kelas_s.values, Hraw.values
kelas_graf = rt.kelas_jalan(gdf[st_col]) if st_col != "(tidak ada)" else pd.Series("lainnya", index=gdf.index)
G = pm.graf(gdf, tuple(kelas_graf), tuple(sorted(rt.KECEPATAN.items())), S["rt_sig"], 5)

# =============================================================== daftar titik (dijalankan sebelum peta)
with c_titik:
    st.caption("Tambah titik dengan memilih mode 🏥 ⛺ 🚜 ⛏️ 🏠 lalu klik peta. Di sini Anda bisa mengubah nama, "
               "jumlah penduduk (bobot), atau menghapus baris (pilih baris, tekan Delete).")
    titik_now = st.data_editor(
        S["rt_titik"], column_order=TC, num_rows="dynamic", hide_index=True, width="stretch",
        key=f"rt_ed_{S['rt_ver']}",
        column_config={"jenis": st.column_config.SelectboxColumn(options=rt.JENIS_TITIK, help="depot = alat berat"),
                       "lat": st.column_config.NumberColumn(format="%.6f"),
                       "lon": st.column_config.NumberColumn(format="%.6f"),
                       "bobot": st.column_config.NumberColumn(help="Permukiman: jumlah penduduk/rumah")})
titik_now = titik_now.reindex(columns=TC + ["sumber"])
tv = titik_now.copy()
tv["lat"], tv["lon"] = pd.to_numeric(tv["lat"], errors="coerce"), pd.to_numeric(tv["lon"], errors="coerce")
tv = tv[tv["jenis"].isin(rt.JENIS_TITIK)].dropna(subset=["lat", "lon"])

# =============================================================== hitung urutan (otomatis)
P = set(S["rt_putus"])
L = set(S["rt_lambat"]) - P
ti = pm.olah_titik(G, tv)
od, obs = pm.buat_od(G, ti, R["od"])
algos = ["greedy", "krus"] + (["sa"] if sa_on else []) + (["optimal"] if opt_on and len(P) <= 14 else [])
res, galat = None, None
if P:
    sig_run = pm.md5(sorted(P), sorted(L), bencana, tv.to_csv(index=False), [(o[0], o[1], o[2], o[3]) for o in od],
                     algos, kru, tetap, perkm, S["rt_sig"], round(float(hasil["skor"].sum()), 6))
    res = S.get("rt_res")
    if not res or res.get("sig") != sig_run:
        try:
            with st.spinner("🧮 Menghitung urutan terbaik..."):
                res = pm.jalankan(G, P, L, bencana, ti, od, hasil["skor"], algos, kru, tetap, perkm, 400, 15)
            if res is not None:
                res["sig"], res["obs"] = sig_run, obs
        except Exception as e:
            res, galat = None, str(e)
        S["rt_res"] = res
else:
    S["rt_res"] = None

# =============================================================== 3. PETA + HASIL
with c_main:
    st.markdown("### 3️⃣ Tandai jalan putus, terima hasil")
    kiri, kanan = st.columns([5, 3], gap="medium")

    with kiri:
        mode = st.segmented_control("Klik peta untuk", list(MODE), format_func=MODE.get, default="putus",
                                    key="rt_mode", label_visibility="collapsed") or "putus"
        t1, t2, t3 = st.columns([4, 1.3, 1.7])
        tampil = t1.segmented_control("Tampilan", ["urutan", "animasi", "tempuh"], default="urutan", key="rt_tampil",
                                      format_func={"urutan": "🔢 Urutan perbaikan", "animasi": "🎬 Animasi perbaikan",
                                                   "tempuh": "🚗 Waktu tempuh"}.get,
                                      label_visibility="collapsed") or "urutan"
        if t2.button("🎯 Pusatkan", key="rt_pusat", help="Kembali ke seluruh data"):
            S["rt_view"] = None
            S["rt_mapver"] += 1
            st.rerun()
        if t3.button("🧹 Hapus gangguan", key="rt_hapus"):
            S["rt_putus"], S["rt_lambat"], S["rt_res"] = set(), set(), None
            st.rerun()

        best = res["terbaik"] if res else None
        jd = res["jad"][best].sort_values("selesai_jam") if res else None
        rep, tbl, jalur, pilih_od = frozenset(), None, None, 0
        if tampil == "tempuh":
            if not res or not res["M"].od:
                st.info("Waktu tempuh butuh jalan putus, minimal 1 RS dan 1 permukiman (atau unggahan perjalanan).")
                tampil = "urutan"
            else:
                M = res["M"]
                u1, u2 = st.columns(2)
                opsi = ["Sebelum perbaikan"] + [f"Setelah #{int(r.urutan)}: {nama[int(r.ruas)]}" for r in jd.itertuples()]
                tahap = u1.selectbox("Tahap perbaikan", range(len(opsi)), format_func=opsi.__getitem__, key="rt_tahap")
                pilih_od = u2.selectbox("Rute perjalanan", range(len(M.od)), format_func=lambda k: M.od[k][0],
                                        key="rt_pilod")
                rep = frozenset(int(i) for i in jd["ruas"].iloc[:tahap])
                tbl, jalur = rt.bandingkan_od(M, rep)
                tbl["observasi_menit"] = res["obs"]

        if tampil == "animasi" and not res:
            st.info("Animasi butuh minimal 1 ruas putus. Pilih **🔴 Putus** lalu klik ruas di peta, atau gambar area banjir.")
            tampil = "urutan"

        m = peta.dasar(g, S["rt_view"])
        peta.jalan_krusial(m, g)
        peta.gangguan(m, gdf, nama, P, L, rep)
        out = None
        if tampil == "animasi":
            tp_ = ti["tp"]
            src = tp_[tp_["jenis"] == "quarry"]
            if src.empty:
                src = tp_[tp_["jenis"] == "depot"]
            kunci_an = pm.md5(res["sig"], best, kru, [(r.nama, r.node) for r in src.itertuples()])
            if S.get("rt_an_kunci") != kunci_an:
                S["rt_an"] = an.siapkan(res["M"], jd, nama, gdf.geometry.reset_index(drop=True), res["tit"][best], src)
                S["rt_an_kunci"] = kunci_an
            if len(tv):
                peta.pin(m, tv)
            an.Animasi(S["rt_an"]).add_to(m)
            folium.LayerControl(collapsed=True).add_to(m)
            components.html(m.get_root().render(), height=640)
            if src.empty:
                st.info("Belum ada **quarry** (atau alat berat). Animasi hanya menampilkan pengerjaan tiap ruas. "
                        "Tambahkan quarry lewat mode ⛏️ di tampilan Urutan perbaikan, lalu kembali ke sini.")
            else:
                st.caption("Truk berangkat dari quarry ke tiap ruas putus lewat jalan yang sudah terbuka, "
                           "lalu tim memperbaiki sampai ruas berubah hijau. Waktu animasi dipercepat; "
                           "fase truk berangkat dibuat lebih lambat agar terlihat.")
        else:
            if tampil == "tempuh":
                peta.tempuh(m, gdf, res["M"], tbl, jalur, pilih_od)
            elif res:
                peta.lencana(m, gdf, {int(r.ruas): int(r.urutan) for r in jd.itertuples()})
            if len(tv):
                peta.pin(m, tv)
            peta.alat_gambar(m)
            folium.LayerControl(collapsed=True).add_to(m)
            out = st_folium(m, height=600, use_container_width=True, key=f"rt_map_{S['rt_sig']}_{S['rt_mapver']}",
                            returned_objects=["last_clicked", "all_drawings", "center", "zoom"])
        st.markdown(gaya.legenda([
            (sc.WARNA["Rawan tinggi"], f"Banjir rawan tinggi (≥{b_merah:.0%})", False),
            (sc.WARNA["Rawan sedang"], f"Rawan sedang (≥{b_kuning:.0%})", False),
            (sc.WARNA["Aman"], "Aman", False),
            (gaya.KORAL, "Putus", True), (gaya.AMBER, "Terganggu", True), (gaya.HIJAU, "Sudah diperbaiki", False)]),
            unsafe_allow_html=True)
        if tampil == "urutan":
            st.caption("Lingkaran putih bernomor di peta = urutan perbaikan.")

        besar = max(len(c) for c in nx.connected_components(G)) / G.number_of_nodes()
        if besar < .9:
            st.warning(f"Jaringan jalan terpecah-pecah (komponen terbesar baru {besar:.0%}). Ruas yang bersilangan di "
                       "tengah garis perlu dipecah di persimpangan (QGIS: Split with lines).")

        # --- tangani interaksi peta
        ubah = False
        if out:
            if out.get("zoom") and out.get("center"):
                S["rt_view"] = ([out["center"]["lat"], out["center"]["lng"]], out["zoom"])
            klik = out.get("last_clicked")
            if klik and (klik["lat"], klik["lng"]) != S.get("rt_klik"):
                S["rt_klik"] = (klik["lat"], klik["lng"])
                if mode in STATUS_MODE:
                    i = pm.terdekat(S["rt_utm"], klik["lat"], klik["lng"], out.get("zoom"))
                    if i is not None:
                        set_status([i], mode)
                        ubah = True
                else:
                    base = titik_now.reindex(columns=TC + ["sumber"])
                    k = int((base["jenis"] == mode).sum()) + 1
                    baru = pd.DataFrame([{"nama": f"{peta.LABEL[mode]} {k}", "jenis": mode, "lat": klik["lat"],
                                          "lon": klik["lng"], "bobot": 1.0, "sumber": "manual"}])
                    S["rt_titik"] = pd.concat([base, baru], ignore_index=True)
                    S["rt_ver"] += 1
                    ubah = True
            for f in out.get("all_drawings") or []:
                kunci = pm.md5(f.get("geometry"))
                if kunci in S["rt_draw_done"] or not f.get("geometry"):
                    continue
                S["rt_draw_done"].add(kunci)
                set_status(pm.kena(S["rt_utm"], [shape(f["geometry"])]), mode if mode in STATUS_MODE else "putus")
                ubah = True
        if ubah:
            st.rerun()

    with kanan:
        if galat:
            st.error(f"Perhitungan gagal: {galat}")
        elif not P:
            st.markdown(
                "<div class='kosong'><span class='em'>🚧</span><b>Belum ada jalan putus.</b><br><br>"
                "Pilih <b>🔴 Putus</b> lalu klik ruas di peta, atau gambar area banjir/longsor dengan ikon poligon "
                "di kiri atas peta. Bisa juga unggah area gangguan di langkah 1.<br><br>"
                "Hasil urutan perbaikan langsung muncul di sini.</div>", unsafe_allow_html=True)
            if not len(tv):
                st.caption("Tips: tambahkan RS, posko, alat berat, quarry, dan permukiman lewat mode 🏥 ⛺ 🚜 ⛏️ 🏠 "
                           "agar urutan memperhitungkan akses darurat.")
        elif res:
            rg = res["rg"]
            if tampil in ("urutan", "animasi"):
                b = rg[best]
                st.markdown(
                    "<div class='ringkas'>"
                    f"<div><b>{gaya.jam_teks(b['selesai'])}</b><span>selesai semua</span></div>"
                    f"<div><b>{gaya.jam_teks(b['t50'])}</b><span>50% pulih</span></div>"
                    f"<div><b>{gaya.jam_teks(b['t90'])}</b><span>90% pulih</span></div></div>", unsafe_allow_html=True)
                if "krus" in rg and best != "krus" and rg["krus"]["luas"] > 0:
                    pct = 100 * (1 - b["luas"] / rg["krus"]["luas"])
                    if pct >= 1:
                        st.markdown(gaya.chip(f"✨ Kerugian {pct:.0f}% lebih kecil dari urut krusialitas biasa", "ok"),
                                    unsafe_allow_html=True)
                st.caption(f"Urutan dari {pm.NAMA_ALGO[best]} · {len(P)} ruas putus, {len(L)} terganggu · "
                           f"{int(kru)} tim kerja · banjir")
                with st.container(height=330):
                    for r in jd.sort_values("urutan").itertuples():
                        extra = " · ⚠️ alat berat belum bisa sampai" if r.akses_darurat else ""
                        st.markdown(
                            f"<div class='langkah'><div class='no'>{int(r.urutan)}</div><div>"
                            f"<div class='jdl'>{nama[int(r.ruas)]}</div><div class='sub'>"
                            f"jam {r.mulai_jam:.0f}–{r.selesai_jam:.0f} · pulih {r.pulih_sesudah_pct:.0f}%{extra}"
                            f"</div></div></div>", unsafe_allow_html=True)
                grid = np.linspace(0, max(v["selesai"] for v in rg.values()) or 1.0, 80)
                kv = {pm.NAMA_ALGO[a]: rt.kurva_grid(res["tit"][a], grid) for a in res["algos"] if a in ("krus", best)}
                st.line_chart(pd.DataFrame(kv, index=pd.Index(grid, name="jam sejak bencana")), height=170,
                              y_label="Pemulihan (%)")
                jj = jd.sort_values("urutan").copy()
                jj.insert(2, "nama_ruas", [nama[int(i)] for i in jj["ruas"]])
                jj.insert(3, "nomor_ruas", [no_ruas[int(i)] for i in jj["ruas"]])
                jj.insert(4, "nama_jalan", [jalan[int(i)] for i in jj["ruas"]])
                gj = gdf.loc[jj["ruas"].astype(int), ["geometry"]].copy()
                gj["urutan"], gj["nama"] = jj["urutan"].values, jj["nama_ruas"].values
                gj["mulai_jam"], gj["selesai_jam"] = jj["mulai_jam"].values, jj["selesai_jam"].values
                d1, d2 = st.columns(2)
                d1.download_button("⬇️ CSV", jj.to_csv(index=False).encode("utf-8"), "urutan_perbaikan.csv", "text/csv")
                d2.download_button("⬇️ GeoJSON", gj.to_json().encode("utf-8"), "urutan_perbaikan.geojson")
            else:
                mk = tbl[["normal_menit", "terdampak_menit", "pulih_menit"]]
                f1 = lambda s: "-" if s.notna().sum() == 0 else f"{s.mean():.1f}"
                st.markdown(
                    "<div class='ringkas'>"
                    f"<div><b>{f1(mk['normal_menit'])}</b><span>menit, normal</span></div>"
                    f"<div><b>{f1(mk['terdampak_menit'])}</b><span>menit, terdampak</span></div>"
                    f"<div><b>{f1(mk['pulih_menit'])}</b><span>menit, sekarang</span></div></div>", unsafe_allow_html=True)
                st.markdown(gaya.chip(f"🚫 {int(tbl['pulih_menit'].isna().sum())} perjalanan terputus",
                                      "warn" if tbl["pulih_menit"].isna().any() else "ok"), unsafe_allow_html=True)
                tbl["tambahan_menit"] = tbl["pulih_menit"] - tbl["normal_menit"]
                st.dataframe(tbl.sort_values("tambahan_menit", ascending=False, na_position="first")[
                    ["perjalanan", "normal_menit", "terdampak_menit", "pulih_menit", "observasi_menit"]],
                    hide_index=True, width="stretch", height=300,
                    column_config={c: st.column_config.NumberColumn(format="%.1f") for c in
                                   ["normal_menit", "terdampak_menit", "pulih_menit", "observasi_menit"]})
                ob = tbl.dropna(subset=["observasi_menit"])
                if len(ob):
                    st.caption(f"Waktu observasi ÷ model normal (median): "
                               f"{(ob['observasi_menit'] / ob['normal_menit']).median():.2f}. "
                               "Jauh dari 1 berarti kecepatan asumsi perlu dikalibrasi.")
                st.caption("Garis lurus asal→tujuan: hijau ≤1,1× normal, kuning ≤1,5×, oranye ≤2,5×, merah lebih, "
                           "ungu tua terputus. Rute: biru normal, ungu terdampak, hijau sekarang.")

# =============================================================== lain-lain
with c_lain:
    tabel = hasil.sort_values("peringkat")
    topn = st.number_input("Tampilkan N ruas teratas", 1, len(tabel), min(25, len(tabel)), key="rt_topn")
    kolom_tabel = ["peringkat", "ruas", "nama_jalan", "prob_banjir", "kelas", "skor", "yakin_nama_pct"]
    st.dataframe(tabel[kolom_tabel].head(int(topn)), hide_index=True, width="stretch", column_config={
        "prob_banjir": st.column_config.ProgressColumn("prob. banjir (fl_max)", min_value=0.0, max_value=1.0,
                                                       format="%.2f"),
        "skor": st.column_config.NumberColumn("skor", format="%.3f"),
        "yakin_nama_pct": st.column_config.NumberColumn("keyakinan nama (%)", format="%.0f",
                                                        help="Seberapa konsisten nama jalan OSM sepanjang ruas ini")})
    g1, g2 = st.columns(2)
    g1.download_button("⬇️ Tabel ruas (CSV)", tabel.to_csv(index=False).encode("utf-8"), "krusialitas_ruas.csv",
                       "text/csv")
    g2.download_button("⬇️ Peta ruas (GeoJSON)", g.to_json().encode("utf-8"), "krusialitas_ruas.geojson")
    st.markdown("""
**Warna jalan:** dari probabilitas banjir `fl_max` saja. Hijau = aman, kuning = rawan sedang, merah = rawan tinggi.
Batas kuning dan merah bisa diubah di Pengaturan lanjutan.

**Nama jalan:** inputan hanya nomor ruas; nama jalan dicari dari OpenStreetMap lewat kecocokan posisi (jalan OSM terdekat,
maksimal 25 m, suara terbanyak di sepanjang ruas). Kolom *keyakinan nama* menunjukkan seberapa konsisten hasilnya.

**Urutan perbaikan:** tiap langkah memilih ruas yang paling menurunkan kerugian per jam kerja (greedy). Kerugian
menggabungkan akses ke RS/posko, akses alat berat, waktu tempuh perjalanan, keterhubungan jaringan, dan probabilitas banjir.

**Rujukan:** Gorani & Dong, *Optimization of Equitable Routes to Critical Facilities After Flood Events*;
Moghtadernejad, Adey & Hackl (2022), *Determination of Postdisaster Restoration Programs for Road Networks Using a
Double-Stage Optimization Approach*, J. Infrastruct. Syst. (doi:10.1061/(ASCE)IS.1943-555X.0000700);
*Long-term scheduling for road network disaster recovery*, IJDRR 42 (2020) 101353; *Methodology for planning
reconstruction activities after a disaster considering interdependencies and priorities*;
*Restoration of services in disrupted infrastructure systems: A network science approach* (PMC5812613).

**Batasan:** kecepatan jalan dan durasi perbaikan adalah asumsi, bukan angka dari jurnal. Waktu tempuh tanpa kemacetan
dan kapasitas jalan. Animasi memakai jadwal yang sama dengan urutan perbaikan; jalur truk dari quarry mengikuti jalan
yang sudah terbuka pada saat tim berangkat.
""")
