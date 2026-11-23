import json
import hashlib

import folium
import networkx as nx
import numpy as np
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from pyproj import Transformer
from shapely.geometry import shape
from streamlit_folium import st_folium

import animasi as an
import ekspor as ek
import fase as fs
import gaya
import ingest
import nama_jalan as nj
import pemulihan as pm
import peta
import restorasi as rt
import scoring as sc
import simulasi as sim
import skenario as sk

st.set_page_config(page_title="Jalan Siaga", page_icon="🛣️", layout="wide", initial_sidebar_state="collapsed")
gaya.terapkan()
S = st.session_state

bencana = "banjir"        # aplikasi ini khusus banjir
TC = ingest.TITIK_COLS
JENIS_LBL = {"rs": "🏥 RS", "posko": "⛺ posko", "depot": "🚜 alat berat", "quarry": "⛏️ quarry", "permukiman": "🏠 permukiman"}
STATUS_LBL = {"putus": "🔴 Rusak (perlu perbaikan)", "tergenang": "🔵 Tergenang (surut sendiri)",
              "lambat": "🟠 Terganggu (lambat)", "normal": "✅ Normal"}
TITIK_LBL = {"rs": "🏥 RS", "posko": "⛺ Posko", "depot": "🚜 Alat berat", "quarry": "⛏️ Quarry", "permukiman": "🏠 Permukiman",
             "sungai": "🌊 Masukan sungai", "pompa": "🔧 Pompa (bagaimana jika)", "drainase": "🕳️ Drainase dibersihkan (bagaimana jika)",
             "tinggikan": "⬆️ Tinggikan ruas (bagaimana jika)"}
POLA_LBL = dict(sim.POLA_HUJAN, csv="Dari file CSV (mm per jam)")
AMC_LBL = {"kering": "Kering (AMC I)", "normal": "Normal (AMC II)", "basah": "Basah / habis hujan (AMC III)"}
KDR_LBL = {"baik": "Baik", "sedang": "Sedang (sebagian tersumbat)", "tersumbat": "Buruk (banyak tersumbat)"}
PRESET = {
    "Pinggiran kota": {"rt_cn": 85, "rt_drain": 8.0, "rt_manning": 0.05, "rt_tetap": 3.0, "rt_perkm": 6.0},
    "Kota padat": {"rt_cn": 92, "rt_drain": 14.0, "rt_manning": 0.035, "rt_tetap": 3.0, "rt_perkm": 6.0},
    "Perdesaan": {"rt_cn": 75, "rt_drain": 3.0, "rt_manning": 0.07, "rt_tetap": 4.0, "rt_perkm": 8.0},
}
PARAM_KEYS = ["rt_preset", "rt_hujan", "rt_durh", "rt_pola", "rt_amc", "rt_cn", "rt_drain", "rt_kdrain", "rt_manning",
              "rt_surut", "rt_gridmaks", "rt_amb_t", "rt_amb_g", "rt_amb_r", "rt_penuh", "rt_mutu", "rt_tetap", "rt_perkm",
              "rt_sedimen", "rt_tglama", "rt_kru", "rt_alat", "rt_alpha", "rt_pasang_on", "rt_pasang_sisi", "rt_pasang_puncak",
              "rt_pasang_rentang", "rt_pasang_jam", "rt_sungai_q", "rt_sungai_tp", "rt_sungai_lama"]


def _preset():
    for k, v in PRESET[S["rt_preset"]].items():
        S[k] = v


def md5(*a):
    return pm.md5(*a)


st.markdown("<div class='hero'><h1>🛣️ Jalan Siaga</h1>"
            "<p>Unggah peta jalan dan DEM, jalankan simulasi hujan, lihat ruas mana yang tergenang atau rusak, "
            "lalu atur tim dan alat berat untuk penanganannya.</p></div>", unsafe_allow_html=True)
c_job = st.container()
tab_data, tab_sim, tab_peta, tab_hasil = st.tabs(["📂 Data", "🌧️ Simulasi banjir", "🗺️ Peta dan penanganan", "📊 Hasil dan unduhan"])
with tab_peta:
    kiri, kanan = st.columns([5, 3], gap="medium")
    tb_perb, tb_banjir, tb_tim, tb_jika = kanan.tabs(["🔧 Perbaikan", "🌊 Selama banjir", "👷 Tim dan alat", "🛠️ Bagaimana jika"])


def set_status(idx, status):
    P, L, T = set(S["rt_putus"]), set(S["rt_lambat"]), set(S["rt_tergenang"])
    idx = {int(i) for i in idx}
    P -= idx
    L -= idx
    T -= idx
    if status == "putus":
        P |= idx
    elif status == "lambat":
        L |= idx
    elif status == "tergenang":
        T |= idx
    S["rt_putus"], S["rt_lambat"], S["rt_tergenang"] = P, L, T
    S["rt_asal_sim"] = None             # tanda diubah manual: hasil fase tidak lagi persis dari simulasi


# =============================================================== 1. UNGGAH
with tab_data:
    with st.container(key="kartu_unggah"):
        st.markdown("### 1️⃣ Unggah")
        fs_ = st.file_uploader(
            "Seret semua file sekaligus. Jalan wajib. Yang lain boleh menyusul: RS, posko, alat berat, quarry, permukiman, "
            "area banjir, DEM (.tif), tutupan lahan (poligon dengan kolom cn atau kelas lahan), garis sungai, hujan per jam (CSV), "
            "jejak banjir (CSV lat, lon, tinggi), data perjalanan, atau file proyek (.json). Perannya dibaca otomatis.",
            accept_multiple_files=True, type=["geojson", "json", "gpkg", "zip", "csv", "xlsx", "tif", "tiff"], key="up")
        if st.button("🎈 Coba dengan data contoh", key="btn_demo"):
            S["demo"] = True
        demo = bool(S.get("demo")) and not fs_

        items = []
        for f in fs_ or []:
            try:
                tebak, obj = ingest.baca_berkas(f, S.get("rt_epsg", 32749))
            except Exception as e:
                st.error(f"{f.name}: tidak bisa dibaca ({e})")
                continue
            pilih = S.get(f"rt_peran_{f.name}", "otomatis")
            items.append((f.name, tebak if pilih == "otomatis" else pilih, obj, tebak))

        if demo:
            g0, t0, p0 = ingest.contoh()
            R = {"jalan": g0, "titik": t0, "gangguan": [], "od": None, "catatan": [], "dem": None, "lahan": [],
                 "sungai": None, "hujan": None, "jejak": None, "proyek": None}
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
        if R.get("dem") is not None:
            chips.append(gaya.chip("🏔️ DEM terbaca", "ok"))
        if R["lahan"]:
            chips.append(gaya.chip(f"🌾 tutupan lahan × {len(R['lahan'])}", "ok"))
        if R["sungai"] is not None:
            chips.append(gaya.chip(f"〰️ sungai × {len(R['sungai'])}", "ok"))
        if R["hujan"]:
            chips.append(gaya.chip(f"🌧️ hujan CSV: {sum(R['hujan']):.0f} mm / {len(R['hujan'])} jam", "ok"))
        if R["jejak"] is not None:
            chips.append(gaya.chip(f"📍 jejak banjir × {len(R['jejak'])}", "ok"))
        if R["proyek"]:
            chips.append(gaya.chip("💾 proyek terbaca", "ok"))
        chips += [gaya.chip(c_, "warn") for c_ in R["catatan"]]
        st.markdown("".join(chips), unsafe_allow_html=True)
        if demo:
            st.caption("Data contoh (lokasi fiktif). Peta otomatis membuka lokasi data yang Anda unggah nanti, di mana pun.")
        if R["gangguan"]:
            st.segmented_control("Area gangguan yang diunggah dianggap", ["putus", "tergenang", "lambat"],
                                 format_func={"putus": "🔴 Rusak", "tergenang": "🔵 Tergenang", "lambat": "🟠 Terganggu"}.get,
                                 default="putus", key="rt_gstat")
        if items:
            with st.expander("Salah baca? Ubah peran file"):
                for nm, _, _, tebak in items:
                    st.selectbox(f"{nm} (terbaca: {tebak})", ingest.PERAN, key=f"rt_peran_{nm}")

# =============================================================== reset hanya bila jaringan jalan berganti
kol = [c for c in gdf.columns if c != "geometry"]
pil = lambda rol: [c for c in kol if ingest.tebak_kolom(c) == rol]
sig_jalan = md5(len(gdf), [round(float(v), 5) for v in gdf.total_bounds], bool(demo))
if S.get("rt_sig") != sig_jalan:
    for k in [k for k in S if k.startswith("rt_") and k != "rt_epsg" and not k.startswith("rt_peran_")]:
        del S[k]
    S["rt_sig"] = sig_jalan
    S["rt_utm"] = pm.utm_dari(gdf)
    S["rt_putus"], S["rt_lambat"], S["rt_tergenang"] = (set(p0) if demo else set()), set(), set()
    S["rt_rilis"], S["rt_twin"], S["rt_tdur"], S["rt_asal_sim"] = {}, {}, {}, None
    S["rt_titik"] = pd.DataFrame(columns=TC + ["sumber"])
    S["rt_titik_now"] = S["rt_titik"]
    S["rt_sungai"] = pd.DataFrame(columns=["nama", "lat", "lon", "q", "tp", "lama"])
    S["rt_interv"] = pd.DataFrame(columns=["nama", "jenis", "lat", "lon", "ruas", "nilai", "radius_m"])
    S["rt_ver"], S["rt_sver"], S["rt_iver"], S["rt_mapver"], S["rt_view"], S["rt_klik"] = 0, 0, 0, 0, None, None
    S["rt_draw_done"], S["rt_res"] = set(), None
    S["rt_cache"] = sk.Cache(10)
    S["rt_titik_sig"] = None
    S["rt_gsig"] = None
    S["rt_proj_sig"] = None
DEFAULTS = {"rt_preset": "Pinggiran kota", "rt_hujan": 150, "rt_durh": 3.0, "rt_pola": "tengah", "rt_amc": "normal", "rt_kdrain": "baik",
            "rt_surut": 6.0, "rt_gridmaks": 250, "rt_mutu": "cepat", "rt_amb_t": sim.AMBANG_AWAL[0], "rt_amb_g": sim.AMBANG_AWAL[1],
            "rt_amb_r": sim.AMBANG_AWAL[2], "rt_penuh": 0.5, "rt_sedimen": 4.0, "rt_tglama": 12.0, "rt_bk": sc.BATAS_AWAL[0],
            "rt_bm": sc.BATAS_AWAL[1], "rt_sungai_q": 20.0, "rt_sungai_tp": 2.0, "rt_sungai_lama": 8.0, "rt_pasang_sisi": "Selatan",
            "rt_pasang_puncak": 0.8, "rt_pasang_rentang": 1.0, "rt_pasang_jam": 3.0, "rt_kru": 2, "rt_alat": 1, "rt_alpha": 0.75,
            "rt_pakai_fl": True, "rt_simt": "Maksimum", "rt_dasar": "persen"}
for k_, v_ in {**PRESET["Pinggiran kota"], **DEFAULTS}.items():
    S.setdefault(k_, v_)
S.setdefault("rt_epsg", 32749)
_fl0, _id0, _kj0 = ingest.kolom_banjir(kol), next(iter(pil("id")), None), next(iter(pil("jalan")), None)
S.setdefault("rt_fl", _fl0 if _fl0 is not None else "(tidak ada)")
S.setdefault("rt_idc", _id0 if _id0 is not None else "(nomor urut)")
S.setdefault("rt_kj", _kj0 if _kj0 is not None else "(tidak ada)")

# titik dari file: gabungkan dengan titik manual (menambah file tidak menghapus tanda manual)
t_file = R["titik"] if R["titik"] is not None else pd.DataFrame(columns=TC + ["sumber"])
sig_tf = md5(t_file.to_csv(index=False), bool(demo))
if S.get("rt_titik_sig") != sig_tf:
    man = S["rt_titik_now"]
    man = man[man["sumber"] == "manual"] if "sumber" in man.columns else man.iloc[0:0]
    S["rt_titik"] = pd.concat([t_file.reindex(columns=TC + ["sumber"]), man.reindex(columns=TC + ["sumber"])], ignore_index=True)
    S["rt_ver"] += 1
    S["rt_titik_sig"] = sig_tf

sig_g = md5(sig_jalan, [(n_, len(d_)) for n_, d_ in R["gangguan"]], S.get("rt_gstat", "putus"))
if R["gangguan"] and S.get("rt_gsig") != sig_g:           # area gangguan dari file -> status ruas
    default = S.get("rt_gstat", "putus")
    for _, d in R["gangguan"]:
        kol_s = next((c for c in d.columns if str(c).lower() in ("status", "kondisi", "gangguan")), None)
        for _, r in d.iterrows():
            st_ = default
            if kol_s is not None:
                v = str(r[kol_s]).lower()
                st_ = "putus" if any(x in v for x in ("putus", "rusak", "tutup", "timbun")) else \
                    "lambat" if any(x in v for x in ("ganggu", "lambat")) else \
                    "tergenang" if any(x in v for x in ("genang", "banjir")) else default
            set_status(pm.kena(S["rt_utm"], [r.geometry]), st_)
    S["rt_gsig"] = sig_g

# =============================================================== buka proyek (sebelum widget dibuat)
proj = R.get("proyek")
if proj and S.get("rt_proj_sig") != md5(json.dumps(proj, sort_keys=True)[:5000], sig_jalan):
    S["rt_proj_sig"] = md5(json.dumps(proj, sort_keys=True)[:5000], sig_jalan)
    sj = proj.get("sig_jalan", {})
    if sj.get("n") != len(gdf):
        st.warning(f"Proyek disimpan untuk {sj.get('n')} ruas, sedangkan jalan yang dibuka {len(gdf)} ruas. "
                   "Tanda ruas tidak dipakai; pengaturan dan titik tetap dibuka.")
    else:
        stt = proj.get("status", {})
        S["rt_putus"], S["rt_lambat"], S["rt_tergenang"] = (set(map(int, stt.get("putus", []))), set(map(int, stt.get("lambat", []))),
                                                            set(map(int, stt.get("tergenang", []))))
        S["rt_rilis"] = {int(k): float(v) for k, v in proj.get("rilis", {}).items()}
        S["rt_twin"] = {int(k): tuple(v) for k, v in proj.get("twin", {}).items()}
        S["rt_tdur"] = {int(k): float(v) for k, v in proj.get("tdur", {}).items()}
        if proj.get("nama_jalan"):
            S["rt_nj"] = pd.DataFrame(proj["nama_jalan"]).set_index("i")
            S["rt_njcat"] = "Nama jalan dibuka dari file proyek."
    for nm_, kol_ in (("titik", "rt_titik"), ("sungai", "rt_sungai"), ("interv", "rt_interv")):
        if proj.get(nm_):
            S[kol_] = pd.DataFrame(proj[nm_])
    S["rt_ver"] += 1
    S["rt_sver"] += 1
    S["rt_iver"] += 1
    for k_, v_ in (proj.get("param") or {}).items():
        if k_ in PARAM_KEYS:
            S[k_] = v_
    S["rt_titik_sig"] = sig_tf

# =============================================================== daftar titik, graf, titik pada graf
with tab_data:
    with st.expander("📍 Daftar titik (RS, posko, alat berat, quarry, permukiman)"):
        st.caption("Tambah titik lewat tab Peta (mode Tambah titik), lalu ubah nama atau jumlah penduduk (bobot) di sini. "
                   "Untuk alat berat dan quarry, bobot = jumlah unit. Hapus baris: pilih lalu tekan Delete.")
        titik_now = st.data_editor(
            S["rt_titik"], column_order=TC, num_rows="dynamic", hide_index=True, width="stretch", key=f"rt_ed_{S['rt_ver']}",
            column_config={"jenis": st.column_config.SelectboxColumn(options=rt.JENIS_TITIK, help="depot = alat berat"),
                           "lat": st.column_config.NumberColumn(format="%.6f"),
                           "lon": st.column_config.NumberColumn(format="%.6f"),
                           "bobot": st.column_config.NumberColumn(help="Permukiman: jumlah penduduk/rumah")})
titik_now = titik_now.reindex(columns=TC + ["sumber"])
titik_now["sumber"] = titik_now["sumber"].fillna("manual")
S["rt_titik_now"] = titik_now
tv = titik_now.copy()
tv["lat"], tv["lon"] = pd.to_numeric(tv["lat"], errors="coerce"), pd.to_numeric(tv["lon"], errors="coerce")
tv = tv[tv["jenis"].isin(rt.JENIS_TITIK)].dropna(subset=["lat", "lon"])

st_auto = next(iter(pil("status")), None)               # kolom fungsi jalan (arteri/kolektor/lokal) dipakai untuk kecepatan
kelas_graf = rt.kelas_jalan(gdf[st_auto]) if st_auto else pd.Series("lainnya", index=gdf.index)
G = pm.graf(gdf, tuple(kelas_graf), tuple(sorted(rt.KECEPATAN.items())), S["rt_sig"], 5)
ti = pm.olah_titik(G, tv)
od, obs = pm.buat_od(G, ti, R["od"])
perm_df = ti["tp"][ti["tp"]["jenis"] == "permukiman"].reset_index(drop=True)
utm_crs = S["rt_utm"].crs
ll2utm = Transformer.from_crs(4326, utm_crs, always_xy=True).transform
utm2ll = Transformer.from_crs(utm_crs, 4326, always_xy=True).transform
if len(perm_df):
    px_, py_ = ll2utm(perm_df["lon"].to_numpy(), perm_df["lat"].to_numpy())
    perm_xy = np.column_stack([px_, py_])
else:
    perm_xy = np.zeros((0, 2))
perm_bobot = perm_df["bobot"].to_numpy() if len(perm_df) else np.zeros(0)
PANJANG = G.graph["panjang"]

# =============================================================== 2. SIMULASI (pengaturan, konteks, tombol)
cache = S["rt_cache"]
with tab_sim:
    ada_dem = demo or R.get("dem") is not None
    if not ada_dem:
        st.info("Belum ada DEM. Unggah raster ketinggian (**GeoTIFF .tif**, mis. DEMNAS 8 m atau SRTM/Copernicus 30 m) di tab Data. "
                "DEM harus menutupi seluruh jalan dan punya CRS. Atau klik **Coba dengan data contoh** untuk melihat cara kerjanya.")
    st.markdown("Aplikasi mengalirkan air di atas **DEM**, menghitung **tinggi genangan**, lalu mengelompokkan tiap ruas: "
                "**terganggu**, **tergenang** (putus sementara, terbuka sendiri saat air surut lewat drainase), atau "
                "**rusak** (air terlalu tinggi, membawa sedimen, jalan tetap putus dan perlu diperbaiki).")
    st.selectbox("Preset kawasan", list(PRESET), key="rt_preset", on_change=_preset,
                 help="Mengisi CN, kapasitas drainase, kekasaran, dan durasi perbaikan dengan nilai awal yang masuk akal. "
                      "Semuanya bisa diubah di Pengaturan rinci.")
    pola_opsi = [k_ for k_ in POLA_LBL if k_ != "csv" or R["hujan"]]
    if S.get("rt_pola") not in pola_opsi:
        S["rt_pola"] = "tengah"
    e1, e2, e3, e4 = st.columns([1, 1, 1.4, 1.4])
    csv_hujan = S["rt_pola"] == "csv" and bool(R["hujan"])
    e1.number_input("Curah hujan total (mm)", 1, 1500, step=10, key="rt_hujan", disabled=csv_hujan,
                    help="Total hujan selama kejadian. 100 mm/hari = lebat sekali, 150+ mm = ekstrem. Dengan CSV, total diambil dari file.")
    e2.number_input("Lama hujan (jam)", 0.5, 72.0, step=0.5, key="rt_durh", disabled=csv_hujan)
    e3.selectbox("Pola hujan", pola_opsi, format_func=POLA_LBL.get, key="rt_pola",
                 help="Hujan nyata punya puncak. Pola seragam membuat genangan puncak cenderung terlalu rendah.")
    e4.selectbox("Kondisi tanah sebelum hujan", list(AMC_LBL), format_func=AMC_LBL.get, key="rt_amc",
                 help="Tanah basah (habis hujan) meloloskan lebih sedikit air, jadi limpasan dan genangan lebih besar.")

    with st.expander("⚙️ Pengaturan rinci (semua asumsi ada di sini; nilai awal dari preset)"):
        st.markdown("**Tanah, drainase, dan aliran**")
        r1, r2, r3, r4 = st.columns(4)
        r1.slider("Curve Number (CN)", 40, 98, key="rt_cn",
                  help="Makin besar makin banyak hujan jadi limpasan. Permukiman padat 90-98, lahan campuran ~80, sawah/hutan 60-70. "
                       "Bila tutupan lahan diunggah, CN ini dipakai di luar poligon lahan.")
        r2.number_input("Kapasitas drainase (mm/jam)", 0.0, 100.0, step=1.0, key="rt_drain",
                        help="Air genangan yang dibuang saluran tiap jam selama masih ada genangan, termasuk sesudah hujan reda. "
                             "Inilah yang membuat genangan surut sendiri.")
        r3.selectbox("Kondisi drainase", list(KDR_LBL), format_func=KDR_LBL.get, key="rt_kdrain",
                     help="Pengali kapasitas drainase: baik 1x, sedang 0,6x, buruk 0,25x.")
        r4.number_input("Kekasaran Manning (n)", 0.01, 0.2, step=0.005, format="%.3f", key="rt_manning")
        r5, r6, r7 = st.columns(3)
        r5.number_input("Pantau sesudah hujan (jam)", 1.0, 72.0, step=1.0, key="rt_surut",
                        help="Lama simulasi dilanjutkan setelah hujan reda untuk melihat genangan surut.")
        r6.number_input("Ukuran grid maksimum (sel)", 100, 500, step=50, key="rt_gridmaks",
                        help="Makin besar makin detail tetapi makin lama.")
        r7.segmented_control("Ketelitian", ["cepat", "teliti"], key="rt_mutu",
                             format_func={"cepat": "⚡ Cepat", "teliti": "🔬 Teliti"}.get,
                             help="Teliti: langkah waktu simulasi lebih kecil dan urutan perbaikan dicari lebih lama (simulated annealing).")
        st.markdown("**Ambang tinggi air pada jalan (m)**")
        a1, a2, a3, a4 = st.columns(4)
        amb_t = a1.number_input("Terganggu bila ≥", 0.02, 2.0, step=0.05, key="rt_amb_t",
                                help="Sekitar 15 cm: mobil kecil mulai sulit lewat.")
        amb_g = a2.number_input("Tergenang (putus sementara) bila ≥", 0.05, 3.0, step=0.05, key="rt_amb_g",
                                help="Sekitar 30 cm: kebanyakan kendaraan berhenti. Terbuka lagi sendiri saat air surut.")
        amb_r = a3.number_input("Rusak (sedimen) bila ≥", 0.1, 5.0, step=0.05, key="rt_amb_r",
                                help="Di atas tinggi ini banjir dianggap membawa lumpur/sedimen dan menggerus jalan: tetap putus sesudah air surut.")
        penuh_m = a4.number_input("Tinggi dianggap 100% rawan (m)", 0.1, 3.0, step=0.1, key="rt_penuh",
                                  help="Dipakai bila hasil simulasi dijadikan pengganti fl_max di tabel peringkat.")
        amb = (min(amb_t, amb_g), amb_g, max(amb_r, amb_g))
        st.markdown("**Penanganan**")
        p1, p2, p3, p4 = st.columns(4)
        p1.number_input("Durasi tetap per ruas (jam)", 0.1, 200.0, step=0.5, key="rt_tetap")
        p2.number_input("Tambahan per km (jam)", 0.0, 200.0, step=1.0, key="rt_perkm")
        p3.number_input("Tambahan per meter air di atas ambang rusak (jam)", 0.0, 100.0, step=1.0, key="rt_sedimen",
                        help="Makin dalam banjirnya, makin banyak sedimen yang harus dibersihkan.")
        p4.number_input("Lama genangan ruas yang ditandai manual (jam)", 1.0, 240.0, step=1.0, key="rt_tglama",
                        help="Dipakai bila ruas 'tergenang' ditandai dengan klik, bukan dari simulasi.")
        st.markdown("**Kolom data jalan**")
        k1, k2, k3, k4 = st.columns(4)
        fl = k1.selectbox("Kolom probabilitas banjir", ["(tidak ada)"] + kol, key="rt_fl",
                          help="Dipakai bila tidak ada simulasi. Bawaan: fl_max.")
        idc = k2.selectbox("Kolom nomor ruas", ["(nomor urut)"] + kol, key="rt_idc")
        kj = k3.selectbox("Kolom nama jalan (jika sudah ada)", ["(tidak ada)"] + kol, key="rt_kj")
        k4.number_input("EPSG bila CRS file tak terbaca", 1000, 99999, key="rt_epsg")
        st.segmented_control("Dasar kelas kekritisan dan warna jalan", ["persen", "ambang"], key="rt_dasar",
                             format_func={"persen": "Persentil peringkat (10/20/30/40%)", "ambang": "Ambang nilai (probabilitas atau tinggi air)"}.get,
                             help="Persentil: 10% ruas teratas Sangat kritis (merah), 20% berikutnya Tinggi (oranye), 30% Sedang (kuning), "
                                  "40% terbawah Rendah (hijau); q = peringkat / jumlah ruas. Bersifat relatif, bukan ambang bahaya absolut.")
        b1, b2 = st.columns(2)
        b_kuning = b1.number_input("Batas kuning (probabilitas fl_max ≥)", 0.01, 0.99, step=0.01, key="rt_bk")
        b_merah = b2.number_input("Batas merah (probabilitas fl_max ≥)", 0.01, 1.0, step=0.01, key="rt_bm")
        ahli = st.checkbox("🧪 Mode ahli: tambahkan faktor lain ke skor peringkat", False, key="rt_ahli",
                           help="Status jalan, konektivitas, densitas permukiman. Bawaan mati: skor hanya dari bahaya banjir.")
        st_col, konek, dens, inv, mode_skor = "(tidak ada)", [], [], False, "mul"
        wh, ws, wk, wd = 5, 3, 4, 3
        if ahli:
            f1_, f2_, f3_ = st.columns(3)
            st_col = f1_.selectbox("Status jalan (arteri/kolektor/lokal)", ["(tidak ada)"] + kol, key="rt_stcol")
            konek = f2_.multiselect("Kolom konektivitas", kol, key="rt_konek")
            dens = f3_.multiselect("Kolom densitas permukiman", kol, key="rt_dens")
            inv = st.checkbox("Konektivitas berupa jarak", False, key="rt_inv")
            mode_skor = st.selectbox("Rumus skor", ["mul", "add"], key="rt_mskor",
                                     format_func={"mul": "Bahaya × Konsekuensi", "add": "Jumlah terbobot"}.get)
            w1, w2, w3, w4 = st.columns(4)
            wh = w1.number_input("Bobot bahaya", 0, 10, 5, key="rt_wh")
            ws = w2.number_input("Bobot status jalan", 0, 10, 3, key="rt_ws")
            wk = w3.number_input("Bobot konektivitas", 0, 10, 4, key="rt_wk")
            wd = w4.number_input("Bobot densitas", 0, 10, 3, key="rt_wd")
        st.caption("Kecepatan jalan dan durasi perbaikan adalah asumsi awal; sesuaikan dengan data BPBD/PU setempat. "
                   "Kecepatan memakai kolom fungsi jalan (arteri/kolektor/lokal) bila ada.")

    with st.expander("🌊 Masukan sungai dan pasang (opsional)"):
        st.markdown("Tepi peta bawaannya hanya jalur keluar. Di sini air bisa **masuk** dari sungai (hidrograf segitiga) atau laut (pasang). "
                    "Tambah titik masukan sungai lewat tab Peta (mode Tambah titik) atau isi tabel.")
        sg_now = st.data_editor(
            S["rt_sungai"], num_rows="dynamic", hide_index=True, width="stretch", key=f"rt_sg_{S['rt_sver']}",
            column_config={"lat": st.column_config.NumberColumn(format="%.6f"), "lon": st.column_config.NumberColumn(format="%.6f"),
                           "q": st.column_config.NumberColumn("debit puncak (m³/s)", min_value=0.0),
                           "tp": st.column_config.NumberColumn("jam puncak", min_value=0.1),
                           "lama": st.column_config.NumberColumn("lama (jam)", min_value=0.5)})
        S["rt_sungai_now"] = sg_now
        if R["sungai"] is not None:
            st.caption("Garis sungai yang diunggah: air masuk dari ujung yang lebih tinggi tiap garis, dengan hidrograf di bawah.")
            h1, h2, h3 = st.columns(3)
            h1.number_input("Debit puncak tiap garis (m³/s)", 0.0, 5000.0, step=5.0, key="rt_sungai_q")
            h2.number_input("Jam puncak", 0.1, 48.0, step=0.5, key="rt_sungai_tp")
            h3.number_input("Lama aliran (jam)", 0.5, 72.0, step=0.5, key="rt_sungai_lama")
        pasang_on = st.checkbox("Ada pasang di salah satu tepi peta", False, key="rt_pasang_on")
        if pasang_on:
            q1, q2, q3, q4 = st.columns(4)
            q1.selectbox("Tepi", list(sk.SISI), key="rt_pasang_sisi")
            q2.number_input("Tinggi puncak pasang di atas dasar tepi terendah (m)", 0.0, 20.0, step=0.1, key="rt_pasang_puncak")
            q3.number_input("Rentang pasang (m)", 0.0, 10.0, step=0.1, key="rt_pasang_rentang")
            q4.number_input("Jam puncak pasang", 0.0, 48.0, step=0.5, key="rt_pasang_jam")

    # ---------------------------------------------------------- konteks (DEM + CN + titik sampel), disimpan
    ctx = None
    if ada_dem:
        dsig = "demo" if demo else md5(len(R["dem"]), hashlib.md5(R["dem"][:1_000_000] + R["dem"][-1_000_000:]).hexdigest())
        ckey = md5(dsig, int(S.get("rt_gridmaks", 250)), S["rt_sig"], [len(x) for x in R["lahan"]])
        if S.get("rt_ctxkey") != ckey:
            try:
                with st.spinner("Menyiapkan DEM dan grid simulasi..."):
                    batas = tuple(float(x) for x in gdf.total_bounds)
                    if demo:
                        z_, dx_, asal_ = sim.dem_contoh(batas, ll2utm, 140)
                    else:
                        z_, dx_, asal_ = sim.baca_dem(R["dem"], utm_crs, batas, int(S.get("rt_gridmaks", 250)))
                    z_ = sim.isi_kosong(z_)
                    cn_peta = None
                    if R["lahan"]:
                        cn_peta = sim.cn_grid([(g.to_crs(utm_crs), sim.cn_poligon(g)) for g in R["lahan"]], z_.shape, dx_, asal_)
                    S["rt_ctx"] = sk.Konteks(z_, dx_, asal_, list(S["rt_utm"].geometry), cn_peta, sig=ckey)
                    S["rt_ctxkey"], S["rt_ctxgalat"] = ckey, None
            except Exception as e:
                S["rt_ctx"], S["rt_ctxkey"], S["rt_ctxgalat"] = None, ckey, str(e)
        ctx = S.get("rt_ctx")
        if S.get("rt_ctxgalat"):
            st.error(f"DEM tidak bisa dipakai: {S['rt_ctxgalat']}")
    if ctx is not None and R["lahan"]:
        n_ = int(np.isfinite(ctx.cn_peta).sum())
        st.caption(f"CN per lokasi dari tutupan lahan: {100 * n_ / ctx.cn_peta.size:.0f}% area punya CN dari peta, sisanya memakai CN di Pengaturan rinci.")

    def param_sim(**ov):
        tabel = R["hujan"] if S["rt_pola"] == "csv" and R["hujan"] else None
        sg = []
        for r_ in S["rt_sungai_now"].itertuples():
            if pd.notna(r_.lat) and pd.notna(r_.lon):
                x_, y_ = ll2utm(float(r_.lon), float(r_.lat))
                sg.append({"x": x_, "y": y_, "q": float(r_.q or 10), "tp": float(r_.tp or 2), "lama": float(r_.lama or 8), "radius_m": 60.0})
        hulu = []
        if R["sungai"] is not None:
            for g in R["sungai"].to_crs(utm_crs).geometry:
                for ln in (g.geoms if g.geom_type == "MultiLineString" else [g]):
                    if ln.geom_type == "LineString" and len(ln.coords) >= 2:
                        hulu.append((*ln.coords[0][:2], *ln.coords[-1][:2]))
        p = sk.param_awal()
        p.update(hujan=float(sum(tabel)) if tabel else float(S["rt_hujan"]), durasi=float(len(tabel)) if tabel else float(S["rt_durh"]),
                 pola="seragam" if tabel else S["rt_pola"], hujan_tabel=tabel, cn=float(S["rt_cn"]), amc=S["rt_amc"],
                 drainase=float(S["rt_drain"]), kondisi_drainase=S["rt_kdrain"], manning=float(S["rt_manning"]),
                 surut_jam=float(S["rt_surut"]), sungai=sg, sungai_hulu=hulu, sungai_q=float(S.get("rt_sungai_q", 20.0)),
                 sungai_tp=float(S.get("rt_sungai_tp", 2.0)), sungai_lama=float(S.get("rt_sungai_lama", 8.0)),
                 cfl=sim.CFL_TELITI if S.get("rt_mutu") == "teliti" else None,
                 pasang={"sisi": sk.SISI[S["rt_pasang_sisi"]], "puncak": float(S["rt_pasang_puncak"]),
                         "rentang": float(S["rt_pasang_rentang"]), "jam_puncak": float(S["rt_pasang_jam"])}
                 if S.get("rt_pasang_on") else None)
        p.update(ov)
        return p

    # ---------------------------------------------------------- hasil tugas latar belakang yang baru selesai
    job = S.get("rt_job")
    if job is not None and job.status != "berjalan" and not job.diproses:
        job.diproses = True
        if job.status == "selesai":
            m_ = job.meta
            if job.jenis == "utama":
                S["rt_simkey"], S["rt_simt"] = m_["kunci"], "Maksimum"
                S["rt_pakai_fl"], S["rt_tampil_sim"] = True, "Simulasi utama"
            elif job.jenis == "skenario":
                S["rt_skn"] = m_
            elif job.jenis == "kalibrasi":
                S["rt_kal"] = m_
            elif job.jenis == "jika":
                S["rt_jika"] = m_
                S["rt_tampil_sim"] = "Bagaimana jika (dengan intervensi)"
            S["rt_joberr"] = None
        elif job.status == "galat":
            S["rt_joberr"] = job.galat
        else:
            S["rt_joberr"] = "Dibatalkan."
    if S.get("rt_joberr"):
        st.warning(f"Tugas terakhir: {S['rt_joberr']}")

    job_aktif = S.get("rt_job") is not None and S["rt_job"].status == "berjalan"
    b1_, b2_ = st.columns([1, 3])
    jalankan = b1_.button("▶️ Jalankan simulasi", key="rt_simrun", type="primary", disabled=ctx is None or job_aktif)
    if ctx is not None:
        pk = param_sim()
        ek_ = md5(ctx.sig, pk["durasi"], pk["surut_jam"], pk["pola"], pk["cfl"])
        if S.get("rt_estk") != ek_:
            S["rt_est"], S["rt_estk"] = sk.perkiraan_detik(ctx, pk), ek_
        sudah = sk.kunci(ctx, pk) in cache
        b2_.caption(f"Grid {ctx.z.shape[1]}×{ctx.z.shape[0]} sel, sel {ctx.dx:.0f} m. Perkiraan waktu ±{S['rt_est']:.0f} detik. "
                    + ("Kombinasi ini sudah dihitung, hasilnya langsung dipakai." if sudah else
                       "Simulasi berjalan di latar belakang dan bisa dibatalkan."))
        if jalankan:
            if sudah:
                S["rt_simkey"], S["rt_simt"], S["rt_pakai_fl"] = sk.kunci(ctx, pk), "Maksimum", True
                S["rt_tampil_sim"] = "Simulasi utama"
            else:
                S["rt_job"] = sk.Job("utama", sk.tugas_dari_param(ctx, [pk]), cache, {"kunci": sk.kunci(ctx, pk)})
            st.rerun()

# =============================================================== hasil simulasi yang ditampilkan
def _ambil(k):
    return cache.get(k) if k else None


opsi_sim = {}
if _ambil(S.get("rt_simkey")) is not None:
    opsi_sim["Simulasi utama"] = S["rt_simkey"]
for mm_, k_ in sorted((S.get("rt_skn") or {}).get("niveau", {}).items()):
    if _ambil(k_) is not None:
        opsi_sim[f"Skenario {float(mm_):g} mm"] = k_
if S.get("rt_jika"):
    if _ambil(S["rt_jika"]["jika"]) is not None:
        opsi_sim["Bagaimana jika (dengan intervensi)"] = S["rt_jika"]["jika"]
    if _ambil(S["rt_jika"]["dasar"]) is not None:
        opsi_sim["Bagaimana jika: pembanding tanpa intervensi"] = S["rt_jika"]["dasar"]
if S.get("rt_tampil_sim") not in opsi_sim:
    S.pop("rt_tampil_sim", None)
sr_key = sr = None
amb = (min(S["rt_amb_t"], S["rt_amb_g"]), S["rt_amb_g"], max(S["rt_amb_r"], S["rt_amb_g"]))
amb_t, amb_g, amb_r = amb


def terapkan(res, key):
    """Hasil simulasi -> tanda ruas: rusak (perlu perbaikan), tergenang (surut sendiri), terganggu; plus waktu dan sedimen."""
    kls = sk.hitung_kelas(res, amb)
    wk = sk.waktu_per_ruas(res, amb)
    ada_seri = res.get("seri") is not None
    S["rt_putus"] = {int(i) for i in np.where(kls == "rusak")[0]}
    S["rt_tergenang"] = {int(i) for i in np.where(kls == "tergenang")[0]}
    S["rt_lambat"] = {int(i) for i in np.where(kls == "lambat")[0]}
    akhir = float(res["waktu_seri"][-1]) if ada_seri else 0.0
    S["rt_rilis"] = {int(i): (float(wk["genang_selesai"][i]) if ada_seri and np.isfinite(wk["genang_selesai"][i]) else akhir)
                     for i in S["rt_putus"]}
    S["rt_twin"] = {int(i): (float(wk["putus_mulai"][i]), float(wk["putus_selesai"][i])) for i in S["rt_tergenang"]
                    if ada_seri and np.isfinite(wk["putus_mulai"][i])}
    S["rt_tdur"] = {int(i): float(S["rt_sedimen"]) * max(float(res["dmaks"][i]) - amb_r, 0.0) for i in S["rt_putus"]}
    S["rt_asal_sim"] = key
    S["rt_res"] = None


with tab_sim:
    if opsi_sim:
        st.markdown("### Hasil simulasi")
        pilih_sim = st.selectbox("Hasil yang ditampilkan di peta", list(opsi_sim), key="rt_tampil_sim")
        sr_key = opsi_sim[pilih_sim]
        sr = _ambil(sr_key)
    if sr:
        pp_ = sr["p"]
        kls_sr = sk.hitung_kelas(sr, amb)
        hs = sr["hs"]
        n_ = {k_: int((kls_sr == k_).sum()) for k_ in sim.KELAS_RUAS}
        wk_sr = sk.waktu_per_ruas(sr, amb)
        pola_t = POLA_LBL.get("csv" if pp_.get("hujan_tabel") else pp_.get("pola", "seragam"), "")
        st.markdown("".join([
            gaya.chip(f"🌧️ {pp_['hujan']:.0f} mm / {pp_['durasi']:g} jam · {pola_t.lower()} · CN {pp_['cn']:.0f} · tanah {pp_['amc']}", "ok"),
            gaya.chip(f"🔴 {n_['rusak']} rusak (≥{amb_r:.2f} m)", "warn" if n_["rusak"] else "ok"),
            gaya.chip(f"🔵 {n_['tergenang']} tergenang (≥{amb_g:.2f} m)", "warn" if n_["tergenang"] else "ok"),
            gaya.chip(f"🟠 {n_['lambat']} terganggu (≥{amb_t:.2f} m)", "warn" if n_["lambat"] else "ok"),
            gaya.chip(f"💧 tinggi maks {hs['h_maks'].max():.2f} m", "ok")]), unsafe_allow_html=True)
        if wk_sr["belum_surut"].any():
            st.caption(f"⏳ {int(wk_sr['belum_surut'].sum())} ruas masih tergenang saat pemantauan berakhir ({hs['horizon_jam']:.0f} jam). "
                       "Perpanjang 'Pantau sesudah hujan' atau naikkan kapasitas drainase.")
        c1, c2 = st.columns([3, 2])
        pilihan_t = ["Maksimum"] + [f"jam {t_:.1f}" for t_ in hs["waktu_jam"]]
        if S.get("rt_simt") not in pilihan_t:
            S["rt_simt"] = "Maksimum"
        c1.select_slider("Genangan pada waktu", pilihan_t, key="rt_simt",
                         help="Maksimum = kedalaman tertinggi selama simulasi. Geser untuk melihat genangan naik dan surut.")
        c2.checkbox("Tampilkan genangan di peta", True, key="rt_simshow")
        st.checkbox("Warnai jalan dengan tinggi banjir simulasi (menggantikan fl_max di warna dan skor)", key="rt_pakai_fl")
        if st.button("✅ Terapkan: tandai ruas rusak, tergenang, dan terganggu dari hasil ini", key="rt_simterap", type="primary",
                     help="Menggantikan tanda sebelumnya. Ruas rusak masuk ke urutan perbaikan (dikerjakan setelah air surut); "
                          "ruas tergenang terbuka sendiri."):
            terapkan(sr, sr_key)
            st.rerun()
        if S.get("rt_asal_sim") == sr_key:
            st.caption("Tanda di peta berasal dari hasil ini.")
        if not hs["selesai"]:
            st.warning("Simulasi dihentikan sebelum waktu habis (batas langkah). Perkecil ukuran grid atau lama hujan.")
        st.caption("Model: limpasan SCS-CN kumulatif, lalu aliran 2D inersia lokal (Bates dkk. 2010) dengan kekasaran Manning; drainase "
                   "membuang genangan terus-menerus. Cocok untuk skrining dan perencanaan; hasil bergantung pada resolusi DEM, CN, "
                   "dan drainase. Bangunan dan gorong-gorong tidak dimodelkan terpisah. Kalibrasi dengan jejak banjir bila ada.")


# =============================================================== metrik skenario (dengan cache)
def metrik(key, res):
    ck = md5(key, amb, sorted(S["rt_putus"]) if False else 0, S["rt_titik_sig"], len(perm_df), len(ti["rs"]), len(ti["posko"]))
    mc = S.setdefault("rt_mcache", {})
    if ck in mc:
        return mc[ck]
    kls = sk.hitung_kelas(res, amb)
    d = {"Tinggi maks (m)": float(res["hs"]["h_maks"].max()), "Rusak": int((kls == "rusak").sum()),
         "Tergenang": int((kls == "tergenang").sum()), "Terganggu": int((kls == "lambat").sum()),
         "Km jalan tergenang/rusak": float(PANJANG[(kls == "tergenang") | (kls == "rusak")].sum() / 1000),
         "Penduduk terdampak": np.nan, "Terisolasi dari RS, puncak (%)": np.nan, "RS terisolasi (maks)": np.nan,
         "Mulai ada warga terisolasi (jam)": np.nan}
    if len(perm_df):
        dep = sim.kedalaman_xy(res["hs"]["h_maks"], res["dx"], res["asal"], perm_xy[:, 0], perm_xy[:, 1])
        d["Penduduk terdampak"] = fs.terdampak(dep, perm_bobot, amb_t)[0]
        if res.get("seri") is not None and (ti["rs"] or ti["posko"]):
            kj_ = fs.kejadian_sim(res["seri"], res["waktu_seri"], np.where(kls == "rusak")[0], amb_t, amb_g, 1.0)
            A = fs.analisis(G, ti, kj_)
            tb = A["tabel"]
            if "pct_terisolasi_rs" in tb:
                d["Terisolasi dari RS, puncak (%)"] = float(tb["pct_terisolasi_rs"].max())
                ada = tb[tb["pop_terisolasi_rs"] > 0]
                d["Mulai ada warga terisolasi (jam)"] = float(ada["jam"].iloc[0]) if len(ada) else np.nan
            d["RS terisolasi (maks)"] = int(tb["rs_terisolasi"].max())
    mc[ck] = d
    return d


# =============================================================== banding skenario, ambang hujan, kalibrasi
with tab_sim:
    if ctx is not None:
        with st.expander("📈 Banding beberapa skenario hujan dan ambang hujan per ruas"):
            st.markdown("Jalankan beberapa total hujan sekaligus (pola, tanah, dan drainase mengikuti pengaturan di atas). "
                        "Hasilnya: tabel perbandingan, peta tiap skenario (pilih di *Hasil yang ditampilkan di peta*), dan "
                        "**ambang hujan per ruas**: ruas mana yang terganggu, tergenang, atau rusak lebih dulu, dan pada hujan berapa mm.")
            niveau = st.multiselect("Total hujan (mm)", [25, 50, 75, 100, 125, 150, 200, 250, 300, 400], default=[50, 100, 150, 200],
                                    key="rt_levels")
            if st.button("▶️ Jalankan skenario", key="rt_skn_run", disabled=len(niveau) < 2 or job_aktif):
                ps = [param_sim(hujan=float(m_)) for m_ in niveau]
                meta = {"niveau": {float(m_): sk.kunci(ctx, p_) for m_, p_ in zip(niveau, ps)}, "aktif": True}
                S["rt_job"] = sk.Job("skenario", sk.tugas_dari_param(ctx, ps), cache, meta)
                st.rerun()
            skn = S.get("rt_skn")
            if skn:
                baris = []
                for mm_, k_ in sorted(skn["niveau"].items()):
                    r_ = _ambil(k_)
                    if r_ is not None:
                        baris.append({"Hujan (mm)": mm_, **metrik(k_, r_)})
                if baris:
                    st.dataframe(pd.DataFrame(baris), hide_index=True, width="stretch", column_config={
                        "Tinggi maks (m)": st.column_config.NumberColumn(format="%.2f"),
                        "Km jalan tergenang/rusak": st.column_config.NumberColumn(format="%.1f"),
                        "Penduduk terdampak": st.column_config.NumberColumn(format="%.0f"),
                        "Terisolasi dari RS, puncak (%)": st.column_config.NumberColumn(format="%.0f"),
                        "Mulai ada warga terisolasi (jam)": st.column_config.NumberColumn(format="%.1f")})
                    if len(baris) >= 2:
                        st.line_chart(pd.DataFrame({"Rusak": [b["Rusak"] for b in baris], "Tergenang": [b["Tergenang"] for b in baris],
                                                    "Terganggu": [b["Terganggu"] for b in baris]},
                                                   index=pd.Index([b["Hujan (mm)"] for b in baris], name="hujan (mm)")),
                                      height=180, y_label="jumlah ruas")
                st.caption("Penduduk terdampak = bobot titik permukiman yang tergenang ≥ ambang terganggu. Kolom isolasi butuh RS atau posko "
                           "dan permukiman di Daftar titik.")

        if R["jejak"] is not None:
            with st.expander("🎯 Kalibrasi dengan jejak banjir"):
                jj_ = R["jejak"]
                jx, jy = ll2utm(jj_["lon"].to_numpy(), jj_["lat"].to_numpy())
                if sr is not None:
                    tb_k, mk = sk.bandingkan_jejak(sr, ctx, jx, jy, jj_["tinggi_m"].to_numpy())
                    tb_k.insert(0, "lokasi", jj_["nama"].values)
                    k1_, k2_, k3_, k4_ = st.columns(4)
                    k1_.metric("RMSE", f"{mk['rmse_m'] * 100:.0f} cm")
                    k2_.metric("Bias (sim − obs)", f"{mk['bias_m'] * 100:+.0f} cm")
                    k3_.metric("Dalam ±15 cm", f"{mk['pct_dalam_15cm']:.0f}%")
                    k4_.metric("Korelasi", "-" if np.isnan(mk["korelasi"]) else f"{mk['korelasi']:.2f}")
                    st.dataframe(tb_k, hide_index=True, width="stretch", column_config={
                        c_: st.column_config.NumberColumn(format="%.2f") for c_ in ("obs_m", "sim_m", "selisih_m")})
                    st.scatter_chart(tb_k.rename(columns={"obs_m": "pengamatan (m)", "sim_m": "simulasi (m)"}),
                                     x="pengamatan (m)", y="simulasi (m)", height=220)
                    st.caption(f"Dibandingkan dengan: {pilih_sim}. Bias positif = simulasi terlalu tinggi; coba turunkan CN atau naikkan drainase.")
                    if st.button("🔍 Cari CN terbaik (6 simulasi)", key="rt_kal_run", disabled=job_aktif):
                        cns = [60, 70, 78, 85, 90, 95]
                        ps = [dict(sr["p"], cn=float(c_)) for c_ in cns]
                        S["rt_job"] = sk.Job("kalibrasi", sk.tugas_dari_param(ctx, ps), cache,
                                             {"cn": {c_: sk.kunci(ctx, p_) for c_, p_ in zip(cns, ps)}})
                        st.rerun()
                    kal = S.get("rt_kal")
                    if kal:
                        rows_ = []
                        for c_, k_ in kal["cn"].items():
                            r_ = _ambil(k_)
                            if r_ is not None:
                                m_ = sk.bandingkan_jejak(r_, ctx, jx, jy, jj_["tinggi_m"].to_numpy())[1]
                                rows_.append({"CN": c_, "RMSE (cm)": m_["rmse_m"] * 100, "bias (cm)": m_["bias_m"] * 100})
                        if rows_:
                            kd = pd.DataFrame(rows_)
                            st.dataframe(kd, hide_index=True, width="content",
                                         column_config={c_: st.column_config.NumberColumn(format="%.0f") for c_ in kd.columns})
                            best = int(kd.loc[kd["RMSE (cm)"].idxmin(), "CN"])
                            st.caption(f"CN dengan galat terkecil: **{best}**.")
                            if st.button(f"Pakai CN {best}", key="rt_kal_pakai"):
                                S["rt_cn"] = best
                                st.rerun()
                else:
                    st.info("Jalankan simulasi dulu, lalu hasilnya dibandingkan dengan jejak banjir.")

# =============================================================== skor (bahaya banjir; mode ahli menambah faktor)
nol = pd.Series(0.0, index=gdf.index)
pakai_sim = sr is not None and bool(S.get("rt_pakai_fl"))
fl = S["rt_fl"]
punya_h = fl != "(tidak ada)" or pakai_sim
Hraw = sc.hazard(gdf, [fl], "max", None, False, True) if fl != "(tidak ada)" else nol
if pakai_sim:
    Hraw = pd.Series(sim.prob_dari_kedalaman(sr["dmaks"], S["rt_penuh"]), index=gdf.index)
Sx = sc.status(gdf[st_col], {"arteri": 1.0, "kolektor": 0.7, "lokal": 0.4, "lainnya": 0.2}) if st_col != "(tidak ada)" else nol
K = sc.rata_minmax(gdf, konek, inv) if konek else nol
D = sc.rata_minmax(gdf, dens) if dens else nol
w = {"h": wh if punya_h else 0, "s": ws if st_col != "(tidak ada)" else 0, "k": wk if konek else 0, "d": wd if dens else 0}
hasil = sc.hitung(Hraw, Sx, K, D, w, mode_skor if punya_h else "add")
mode_persen = S.get("rt_dasar", "persen") == "persen"
if mode_persen:
    kelas_s = sc.kelas_persentil(hasil["peringkat"])
elif pakai_sim:
    kelas_s = sc.kelas_kedalaman(sr["dmaks"], (amb_t, amb_g)).set_axis(gdf.index)
else:
    kelas_s = sc.kelas_banjir(Hraw, (S["rt_bk"], S["rt_bm"]))

kj = S["rt_kj"]
idc = S["rt_idc"]
no_ruas = (gdf[idc].astype(str) if idc != "(nomor urut)" else pd.Series((gdf.index + 1).astype(str), index=gdf.index))
no_ruas = no_ruas.map(lambda x: f"Ruas {x}" if x.strip().isdigit() else x).reset_index(drop=True)
jalan = pd.Series("", index=gdf.index, dtype=object)
if kj != "(tidak ada)":
    jalan = gdf[kj].fillna("").astype(str).str.strip().replace({"nan": "", "None": ""})
yakin = pd.Series(np.nan, index=gdf.index)
if S.get("rt_nj") is not None:
    osm_n = S["rt_nj"]["nama_jalan"].reindex(gdf.index).fillna("")
    kosong_ = jalan == ""
    jalan = jalan.where(~kosong_, osm_n)
    yakin = S["rt_nj"]["yakin_pct"].reindex(gdf.index).where(kosong_)
jalan = jalan.reset_index(drop=True)
nama = no_ruas.where(jalan == "", no_ruas + " · " + jalan)

hasil.insert(0, "ruas", no_ruas)
hasil.insert(1, "nama_jalan", jalan)
hasil["prob_banjir"] = Hraw.values
hasil["persentil_q"] = sc.persentil(hasil["peringkat"]).round(3).values
hasil["kelas"] = kelas_s.values
hasil["yakin_nama_pct"] = yakin.values
g = gdf[["geometry"]].copy()
g["nama"], g["kelas"], g["skor"] = nama.values, kelas_s.values, Hraw.values
if sr is not None:
    g["banjir_m"] = sr["dmaks"]
    wk_ = sk.waktu_per_ruas(sr, amb)
    g["putus_mulai_jam"], g["putus_lama_jam"], g["genang_lama_jam"] = wk_["putus_mulai"], wk_["putus_lama"], wk_["genang_lama"]

# =============================================================== tim dan alat berat (pengaturan; tabel kebutuhan di bawah)
nama_pangkalan = [str(x) for x in ti["tp"].loc[ti["tp"]["jenis"].isin(["depot", "quarry"]), "nama"]] if len(ti["tp"]) else []
with tb_tim:
    st.caption("Tambah tim dan alat berat untuk mempercepat perbaikan. Tim bantuan boleh tiba belakangan (misalnya dari kabupaten lain).")
    t1, t2, t3 = st.columns(3)
    t1.number_input("Tim dasar", 1, 30, step=1, key="rt_kru", help="Tim yang siap sejak jam ke-0.")
    t2.number_input("Alat berat per tim", 0, 10, step=1, key="rt_alat",
                    help="0 = tim bekerja manual (sekitar 0,3× kecepatan satu alat berat).")
    t3.number_input("Efek tambahan alat", 0.3, 1.0, step=0.05, key="rt_alpha",
                    help="Kecepatan tim = jumlah alat ^ nilai ini. 1,0 = dua alat dua kali lebih cepat; 0,75 = sekitar 1,7 kali.")
    tambah = st.data_editor(
        pd.DataFrame(columns=["nama", "alat", "tiba_jam", "pangkalan"]), num_rows="dynamic", hide_index=True, width="stretch",
        key="rt_tambah_tim", column_config={
            "alat": st.column_config.NumberColumn("alat berat", min_value=0, step=1, default=1),
            "tiba_jam": st.column_config.NumberColumn("tiba (jam)", min_value=0.0, default=0.0, help="Jam ke berapa sejak hujan mulai"),
            "pangkalan": st.column_config.SelectboxColumn("pangkalan", options=nama_pangkalan,
                                                          help="Opsional: alat berat/quarry tempat tim berangkat")})
tim_tambah = []
node_pangkalan = dict(zip(ti["tp"]["nama"].astype(str), ti["tp"]["node"])) if len(ti["tp"]) else {}
for r_ in tambah.itertuples():
    if pd.notna(r_.alat) or pd.notna(r_.nama):
        tim_tambah.append({"nama": r_.nama if pd.notna(r_.nama) else None, "alat": float(r_.alat) if pd.notna(r_.alat) else 1.0,
                           "tiba": float(r_.tiba_jam) if pd.notna(r_.tiba_jam) else 0.0,
                           "sumber": {node_pangkalan[r_.pangkalan]} if pd.notna(r_.pangkalan) and r_.pangkalan in node_pangkalan else None})
TIM = rt.buat_tim(int(S["rt_kru"]), int(S["rt_alat"]), tim_tambah, float(S["rt_alpha"]))
tim_sig = [(t_["nama"], t_["alat"], t_["tiba"], sorted(map(str, t_["sumber"])) if t_["sumber"] else None) for t_ in TIM]

# =============================================================== hitung urutan perbaikan (otomatis)
P = set(S["rt_putus"])
L = set(S["rt_lambat"]) - P
T = set(S["rt_tergenang"]) - P
teliti = S.get("rt_mutu") == "teliti"
algos = ["greedy", "krus"] + (["sa"] + (["optimal"] if len(P) <= 14 else []) if teliti else [])
rilis = {i: v for i, v in S["rt_rilis"].items() if i in P}
tdur = {i: v for i, v in S["rt_tdur"].items() if i in P}
res, galat = None, None
if P:
    sig_run = md5(sorted(P), sorted(L), tv.to_csv(index=False), [(o[0], o[1], o[2], o[3]) for o in od], algos, tim_sig,
                  S["rt_tetap"], S["rt_perkm"], S["rt_sig"], round(float(hasil["skor"].sum()), 6), sorted(rilis.items()),
                  sorted(tdur.items()))
    res = S.get("rt_res")
    if not res or res.get("sig") != sig_run:
        try:
            with st.spinner("🧮 Menghitung urutan terbaik..."):
                res = pm.jalankan(G, P, L, bencana, ti, od, hasil["skor"], algos, TIM, S["rt_tetap"], S["rt_perkm"], 400, 15,
                                  rilis=rilis, tambah_dur=tdur)
            if res is not None:
                res["sig"], res["obs"] = sig_run, obs
        except Exception as e:
            res, galat = None, str(e)
        S["rt_res"] = res
else:
    S["rt_res"] = None

# =============================================================== fase sebelum dan selama banjir
if sr is not None and S.get("rt_asal_sim") == sr_key and sr.get("seri") is not None:
    kej = fs.kejadian_sim(sr["seri"], sr["waktu_seri"], sorted(P), amb_t, amb_g, 1.0)
    sumber_kej = "simulasi"
else:
    kej = fs.kejadian_tanda(len(gdf), P, T, L, S["rt_tglama"], S.get("rt_twin"))
    sumber_kej = "tanda"
analisis_fase = None
if (ti["rs"] or ti["posko"]) and len(perm_df) and (P or T or L):
    ak = md5(sumber_kej, sr_key if sumber_kej == "simulasi" else 0, sorted(P), sorted(T), sorted(L), amb, S["rt_titik_sig"],
             tv.to_csv(index=False), S["rt_tglama"], S["rt_sig"])
    fc = S.setdefault("rt_fcache", {})
    if ak not in fc:
        fc.clear()
        fc[ak] = fs.analisis(G, ti, kej)
    analisis_fase = fc[ak]

# =============================================================== 3. PETA + HASIL
with kiri:
    st.markdown("### 3️⃣ Tandai jalan, terima hasil")
    modus = st.segmented_control("Klik peta untuk", ["jalan", "titik"], format_func={"jalan": "🛣️ Tandai jalan", "titik": "📍 Tambah titik"}.get,
                                 default="jalan", key="rt_modus", label_visibility="collapsed") or "jalan"
    status_klik, jenis_klik = "putus", "rs"
    if modus == "jalan":
        status_klik = st.segmented_control("Tandai sebagai", list(STATUS_LBL), format_func=STATUS_LBL.get, default="putus",
                                           key="rt_status", label_visibility="collapsed") or "putus"
    else:
        jenis_klik = st.selectbox("Jenis titik", list(TITIK_LBL), format_func=TITIK_LBL.get, key="rt_jenis_t",
                                  label_visibility="collapsed")
    t1_, t2_, t3_ = st.columns([4, 1.3, 1.7])
    tampil = t1_.segmented_control("Tampilan", ["urutan", "animasi", "tempuh", "banjir"], default="urutan", key="rt_tampil",
                                   format_func={"urutan": "🔢 Urutan", "animasi": "🎬 Animasi", "tempuh": "🚗 Waktu tempuh",
                                                "banjir": "🌊 Selama banjir"}.get,
                                   label_visibility="collapsed") or "urutan"
    if t2_.button("🎯 Pusatkan", key="rt_pusat", help="Kembali ke seluruh data"):
        S["rt_view"] = None
        S["rt_mapver"] += 1
        st.rerun()
    if t3_.button("🧹 Hapus tanda", key="rt_hapus"):
        S["rt_putus"], S["rt_lambat"], S["rt_tergenang"], S["rt_res"] = set(), set(), set(), None
        S["rt_rilis"], S["rt_twin"], S["rt_tdur"], S["rt_asal_sim"] = {}, {}, {}, None
        st.rerun()

    best = res["terbaik"] if res else None
    jd = res["jad"][best].sort_values("selesai_jam") if res else None
    rep, tbl, jalur, pilih_od = frozenset(), None, None, 0
    kf = 0
    if tampil == "tempuh":
        if not res or not res["M"].od:
            st.info("Waktu tempuh butuh ruas rusak, minimal 1 RS dan 1 permukiman (atau unggahan perjalanan).")
            tampil = "urutan"
        else:
            M = res["M"]
            u1, u2 = st.columns(2)
            opsi = ["Sebelum perbaikan"] + [f"Setelah #{int(r.urutan)}: {nama[int(r.ruas)]}" for r in jd.itertuples()]
            tahap = u1.selectbox("Tahap perbaikan", range(len(opsi)), format_func=opsi.__getitem__, key="rt_tahap")
            pilih_od = u2.selectbox("Rute perjalanan", range(len(M.od)), format_func=lambda k: M.od[k][0], key="rt_pilod")
            rep = frozenset(int(i) for i in jd["ruas"].iloc[:tahap])
            tbl, jalur = rt.bandingkan_od(M, rep)
            tbl["observasi_menit"] = res["obs"]
    if tampil == "animasi" and not res:
        st.info("Animasi butuh minimal 1 ruas rusak. Pilih **🔴 Rusak** lalu klik ruas di peta, atau terapkan hasil simulasi.")
        tampil = "urutan"
    if tampil == "banjir":
        nk = len(kej["waktu"])
        if S.get("rt_jamf", 0) >= nk:
            S["rt_jamf"] = 0
        kf = st.select_slider("Keadaan jalan pada jam ke-", options=list(range(nk)), key="rt_jamf",
                              format_func=lambda k: f"jam {kej['waktu'][k]:.0f}")

    m = peta.dasar(g, S["rt_view"])
    peta.jalan_krusial(m, g, sc.KELAS_PERSEN if mode_persen else None)
    if tampil != "banjir":
        peta.gangguan(m, gdf, nama, P, L, rep, T)
    if R["sungai"] is not None:
        peta.sungai_garis(m, R["sungai"])
    if sr is not None and S.get("rt_simshow", True):
        t_pilih = S.get("rt_simt", "Maksimum")
        opsi_t = ["jam %.1f" % t_ for t_ in sr["hs"]["waktu_jam"]]
        h_ = sr["hs"]["bingkai"][opsi_t.index(t_pilih)] if t_pilih in opsi_t else sr["hs"]["h_maks"]
        h_ll, bts = sim.ke_lonlat(h_, sr["dx"], sr["asal"], ll2utm, utm2ll)
        peta.genangan(m, sim.png_bytes(sim.gambar_rgba(h_ll)), bts)
    masuk_df = pd.concat([S["rt_sungai_now"].assign(jenis="sungai")[["nama", "jenis", "lat", "lon"]],
                          S.get("rt_interv_now", S["rt_interv"])[["nama", "jenis", "lat", "lon"]]], ignore_index=True).dropna(subset=["lat", "lon"])
    out = None
    evak = None
    if tampil == "animasi":
        tp_ = ti["tp"]
        src = tp_[tp_["jenis"] == "quarry"]
        if src.empty:
            src = tp_[tp_["jenis"] == "depot"]
        kunci_an = md5(res["sig"], best, [(r.nama, r.node) for r in src.itertuples()])
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
                    "Tambahkan quarry lewat 📍 Tambah titik, lalu kembali ke sini.")
        else:
            st.caption("Truk berangkat dari quarry ke tiap ruas rusak lewat jalan yang sudah terbuka, lalu tim memperbaiki sampai "
                       "ruas berubah hijau. Waktu animasi dipercepat. Ruas yang menunggu air surut baru dikerjakan setelah surut.")
    else:
        if tampil == "tempuh":
            peta.tempuh(m, gdf, res["M"], tbl, jalur, pilih_od)
        elif tampil == "banjir":
            iso_k = analisis_fase["iso_rs"][kf] if analisis_fase is not None else None
            evak = fs.evakuasi(G, ti, kej, kf) if ti["posko"] and len(perm_df) else None
            peta.fase(m, gdf, nama, set(np.where(kej["blok"][kf])[0]), set(np.where(kej["lambat"][kf])[0]), evak, perm_df, iso_k)
        elif res:
            peta.lencana(m, gdf, {int(r.ruas): int(r.urutan) for r in jd.itertuples()})
        if len(tv):
            peta.pin(m, tv)
        if len(masuk_df):
            peta.masukan(m, masuk_df)
        peta.alat_gambar(m)
        folium.LayerControl(collapsed=True).add_to(m)
        out = st_folium(m, height=600, use_container_width=True, key=f"rt_map_{S['rt_sig']}_{S['rt_mapver']}",
                        returned_objects=["last_clicked", "all_drawings", "center", "zoom"])
    if mode_persen:
        jml = kelas_s.value_counts()
        leg = [(sc.WARNA[k_], f"{k_} ({lb_}, {int(jml.get(k_, 0))} ruas)", False) for k_, lb_ in
               zip(sc.KELAS_PERSEN, ("10% teratas", "20% berikutnya", "30% berikutnya", "40% terbawah"))]
    elif sr is not None and S.get("rt_pakai_fl"):
        leg = [(sc.WARNA["Rawan tinggi"], f"Air ≥{amb_g * 100:.0f} cm", False),
               (sc.WARNA["Rawan sedang"], f"{amb_t * 100:.0f}–{amb_g * 100:.0f} cm", False),
               (sc.WARNA["Aman"], f"< {amb_t * 100:.0f} cm", False)]
    else:
        leg = [(sc.WARNA["Rawan tinggi"], f"Banjir rawan tinggi (≥{S['rt_bm']:.0%})", False),
               (sc.WARNA["Rawan sedang"], f"Rawan sedang (≥{S['rt_bk']:.0%})", False), (sc.WARNA["Aman"], "Aman", False)]
    leg += [(gaya.KORAL, f"Rusak (≥{amb_r * 100:.0f} cm, perlu perbaikan)", True), (gaya.AIR, "Tergenang (surut sendiri)", True),
            (gaya.AMBER, "Terganggu", True), (gaya.HIJAU, "Sudah diperbaiki", False)]
    st.markdown(gaya.legenda(leg), unsafe_allow_html=True)
    if sr is not None and S.get("rt_simshow", True):
        st.caption("🌊 Genangan simulasi: biru muda = dangkal, biru tua = dalam, ungu = ≥1,5 m. Arahkan kursor ke ruas untuk melihat "
                   "tinggi banjir, jam mulai terputus, dan lama terputus.")
    if tampil == "urutan":
        st.caption("Lingkaran putih bernomor di peta = urutan perbaikan.")
    if tampil == "banjir":
        st.caption("Biru putus-putus = ruas terblokir pada jam itu, oranye = terganggu, garis hijau = jalur evakuasi ke posko terdekat, "
                   "lingkaran merah = permukiman terisolasi dari RS.")
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
            if modus == "jalan":
                i = pm.terdekat(S["rt_utm"], klik["lat"], klik["lng"], out.get("zoom"))
                if i is not None:
                    set_status([i], status_klik)
                    ubah = True
            elif jenis_klik in rt.JENIS_TITIK:
                base = titik_now.reindex(columns=TC + ["sumber"])
                k = int((base["jenis"] == jenis_klik).sum()) + 1
                baru = pd.DataFrame([{"nama": f"{peta.LABEL[jenis_klik]} {k}", "jenis": jenis_klik, "lat": klik["lat"],
                                      "lon": klik["lng"], "bobot": 1.0, "sumber": "manual"}])
                S["rt_titik"] = pd.concat([base, baru], ignore_index=True)
                S["rt_ver"] += 1
                ubah = True
            elif jenis_klik == "sungai":
                base = S["rt_sungai_now"]
                baru = pd.DataFrame([{"nama": f"Sungai {len(base) + 1}", "lat": klik["lat"], "lon": klik["lng"], "q": 20.0, "tp": 2.0, "lama": 8.0}])
                S["rt_sungai"] = pd.concat([base, baru], ignore_index=True)
                S["rt_sver"] += 1
                ubah = True
            else:
                base = S.get("rt_interv_now", S["rt_interv"])
                ruas_i = pm.terdekat(S["rt_utm"], klik["lat"], klik["lng"], out.get("zoom")) if jenis_klik == "tinggikan" else None
                if jenis_klik != "tinggikan" or ruas_i is not None:
                    baru = pd.DataFrame([{"nama": f"{peta.LABEL[jenis_klik]} {len(base) + 1}", "jenis": jenis_klik, "lat": klik["lat"],
                                          "lon": klik["lng"], "ruas": (ruas_i + 1) if ruas_i is not None else np.nan,
                                          "nilai": {"pompa": 2.0, "drainase": 2.0, "tinggikan": 40.0}[jenis_klik],
                                          "radius_m": {"pompa": 60.0, "drainase": 300.0, "tinggikan": np.nan}[jenis_klik]}])
                    S["rt_interv"] = pd.concat([base, baru], ignore_index=True)
                    S["rt_iver"] += 1
                    ubah = True
        for f in out.get("all_drawings") or []:
            kunci = md5(f.get("geometry"))
            if kunci in S["rt_draw_done"] or not f.get("geometry"):
                continue
            S["rt_draw_done"].add(kunci)
            set_status(pm.kena(S["rt_utm"], [shape(f["geometry"])]), status_klik if modus == "jalan" else "putus")
            ubah = True
    if ubah:
        st.rerun()

# =============================================================== panel kanan: perbaikan
with tb_perb:
    if galat:
        st.error(f"Perhitungan gagal: {galat}")
    elif not P:
        st.markdown("<div class='kosong'><span class='em'>🚧</span><b>Belum ada ruas rusak.</b><br><br>"
                    "Pilih <b>🔴 Rusak</b> lalu klik ruas di peta, gambar area dengan ikon poligon, atau jalankan simulasi lalu "
                    "<b>✅ Terapkan</b>.<br><br>Ruas yang hanya <b>tergenang</b> tidak perlu diperbaiki: tunggu air surut.</div>",
                    unsafe_allow_html=True)
        if not len(tv):
            st.caption("Tips: tambahkan RS, posko, alat berat, quarry, dan permukiman lewat 📍 Tambah titik agar urutan "
                       "memperhitungkan akses darurat.")
        if T:
            st.caption(f"{len(T)} ruas tergenang akan terbuka sendiri saat air surut.")
    elif res:
        rg = res["rg"]
        b = rg[best]
        st.markdown("<div class='ringkas'>"
                    f"<div><b>{gaya.jam_teks(b['selesai'])}</b><span>selesai semua</span></div>"
                    f"<div><b>{gaya.jam_teks(b['t50'])}</b><span>50% pulih</span></div>"
                    f"<div><b>{gaya.jam_teks(b['t90'])}</b><span>90% pulih</span></div></div>", unsafe_allow_html=True)
        if "krus" in rg and best != "krus" and rg["krus"]["luas"] > 0:
            pct = 100 * (1 - b["luas"] / rg["krus"]["luas"])
            if pct >= 1:
                st.markdown(gaya.chip(f"✨ Kerugian {pct:.0f}% lebih kecil dari urut krusialitas biasa", "ok"), unsafe_allow_html=True)
        st.caption(f"Urutan dari {pm.NAMA_ALGO[best]} · {len(P)} ruas rusak, {len(L)} terganggu, {len(T)} tergenang (tak perlu diperbaiki) · "
                   f"{len(TIM)} tim, {sum(t_['alat'] for t_ in TIM):.0f} alat berat · jam dihitung sejak hujan mulai")
        with st.container(height=330):
            for r in jd.sort_values("urutan").itertuples():
                extra = " · ⚠️ alat berat belum bisa sampai" if r.akses_darurat else ""
                tunggu = f" · menunggu air surut (jam {r.rilis_jam:.0f})" if r.rilis_jam > 0.01 and r.mulai_jam <= r.rilis_jam + 0.01 else ""
                st.markdown(f"<div class='langkah'><div class='no'>{int(r.urutan)}</div><div>"
                            f"<div class='jdl'>{nama[int(r.ruas)]}</div><div class='sub'>"
                            f"{r.tim_nama} · jam {r.mulai_jam:.0f}–{r.selesai_jam:.0f} · pulih {r.pulih_sesudah_pct:.0f}%{tunggu}{extra}"
                            f"</div></div></div>", unsafe_allow_html=True)
        grid = np.linspace(0, max(v["selesai"] for v in rg.values()) or 1.0, 80)
        kv = {pm.NAMA_ALGO[a]: rt.kurva_grid(res["tit"][a], grid) for a in res["algos"] if a in ("krus", best)}
        st.line_chart(pd.DataFrame(kv, index=pd.Index(grid, name="jam sejak hujan mulai")), height=170, y_label="Pemulihan (%)")
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
        if tampil == "tempuh":
            mk = tbl[["normal_menit", "terdampak_menit", "pulih_menit"]]
            f1 = lambda s_: "-" if s_.notna().sum() == 0 else f"{s_.mean():.1f}"
            st.markdown("**Waktu tempuh rata-rata**")
            st.markdown("<div class='ringkas'>"
                        f"<div><b>{f1(mk['normal_menit'])}</b><span>menit, normal</span></div>"
                        f"<div><b>{f1(mk['terdampak_menit'])}</b><span>menit, terdampak</span></div>"
                        f"<div><b>{f1(mk['pulih_menit'])}</b><span>menit, sekarang</span></div></div>", unsafe_allow_html=True)
            st.markdown(gaya.chip(f"🚫 {int(tbl['pulih_menit'].isna().sum())} perjalanan terputus",
                                  "warn" if tbl["pulih_menit"].isna().any() else "ok"), unsafe_allow_html=True)
            tbl["tambahan_menit"] = tbl["pulih_menit"] - tbl["normal_menit"]
            st.dataframe(tbl.sort_values("tambahan_menit", ascending=False, na_position="first")[
                ["perjalanan", "normal_menit", "terdampak_menit", "pulih_menit", "observasi_menit"]],
                hide_index=True, width="stretch", height=260,
                column_config={c: st.column_config.NumberColumn(format="%.1f") for c in
                               ["normal_menit", "terdampak_menit", "pulih_menit", "observasi_menit"]})

# =============================================================== panel kanan: selama banjir
with tb_banjir:
    if analisis_fase is None:
        st.info("Butuh minimal 1 RS atau posko, dan 1 permukiman di Daftar titik, serta ruas yang ditandai atau hasil simulasi "
                "yang diterapkan. Fase ini menunjukkan jam ke berapa warga terisolasi dan jalur evakuasi yang masih bisa dilewati.")
    else:
        A = analisis_fase
        tb_ = A["tabel"]
        st.caption("Sumber keadaan jalan: " + ("simulasi yang diterapkan (air naik dan surut tiap jam)." if sumber_kej == "simulasi" else
                                               f"tanda manual (tergenang dan terganggu dianggap {S['rt_tglama']:.0f} jam)."))
        if "pct_terisolasi_rs" in tb_:
            pk_ = tb_.loc[tb_["pct_terisolasi_rs"].idxmax()]
            ada_ = tb_[tb_["pop_terisolasi_rs"] > 0]
            st.markdown("<div class='ringkas'>"
                        f"<div><b>{pk_['pct_terisolasi_rs']:.0f}%</b><span>terisolasi dari RS (puncak, jam {pk_['jam']:.0f})</span></div>"
                        f"<div><b>{'-' if not len(ada_) else 'jam ' + format(ada_['jam'].iloc[0], '.0f')}</b><span>warga pertama terisolasi</span></div>"
                        f"<div><b>{int(tb_['rs_terisolasi'].max())}</b><span>RS terisolasi (maks)</span></div></div>", unsafe_allow_html=True)
        if sr is not None and len(perm_df):
            dep_ = sim.kedalaman_xy(sr["hs"]["h_maks"], sr["dx"], sr["asal"], perm_xy[:, 0], perm_xy[:, 1])
            pt_, nt_ = fs.terdampak(dep_, perm_bobot, amb_t)
            st.markdown(gaya.chip(f"🏠 {pt_:,.0f} penduduk terdampak genangan ({nt_} titik permukiman, ≥{amb_t:.2f} m)", "warn" if nt_ else "ok"),
                        unsafe_allow_html=True)
        kolom_g = [c for c in ("pct_terisolasi_rs", "pct_terisolasi_posko") if c in tb_]
        if kolom_g:
            st.line_chart(tb_.set_index("jam")[kolom_g].rename(columns={"pct_terisolasi_rs": "terisolasi dari RS (%)",
                                                                           "pct_terisolasi_posko": "terisolasi dari posko (%)"}),
                          height=180, x_label="jam sejak hujan mulai")
        st.dataframe(tb_[["jam", "ruas_terblokir", "ruas_terganggu", "pop_terisolasi_rs", "pop_terisolasi_posko", "rs_terisolasi",
                          "menit_ke_rs"]].rename(columns={"ruas_terblokir": "ruas blokir", "ruas_terganggu": "ruas terganggu",
                                                          "pop_terisolasi_rs": "jiwa tanpa akses RS", "pop_terisolasi_posko": "jiwa tanpa akses posko",
                                                          "rs_terisolasi": "RS terisolasi", "menit_ke_rs": "menit ke RS"}),
                     hide_index=True, width="stretch", height=200, column_config={"menit ke RS": st.column_config.NumberColumn(format="%.0f")})
        st.markdown("**Per permukiman**")
        pp_tab = A["perm"].sort_values("lama_isolasi_jam", ascending=False)
        st.dataframe(pp_tab, hide_index=True, width="stretch", height=200, column_config={
            c: st.column_config.NumberColumn(format="%.0f") for c in ("penduduk", "isolasi_mulai_jam", "tersambung_lagi_jam", "lama_isolasi_jam")})
        if evak is not None and len(evak):
            st.markdown(f"**Evakuasi ke posko pada jam {kej['waktu'][kf]:.0f}** (lihat jam di atas peta)")
            ev_t = evak[["permukiman", "penduduk", "menit", "tersambung"]].sort_values("menit", na_position="first")
            st.dataframe(ev_t, hide_index=True, width="stretch", height=180,
                         column_config={"menit": st.column_config.NumberColumn(format="%.0f")})
        d_a, d_b = st.columns(2)
        d_a.download_button("⬇️ Isolasi per jam (CSV)", tb_.to_csv(index=False).encode("utf-8"), "fase_banjir_per_jam.csv", "text/csv")
        d_b.download_button("⬇️ Per permukiman (CSV)", A["perm"].to_csv(index=False).encode("utf-8"), "isolasi_permukiman.csv", "text/csv")
        st.caption("Isolasi = tidak ada jalan terbuka dari permukiman ke RS. Jalur evakuasi memakai kecepatan jalan terganggu bila ruas terganggu.")

# =============================================================== panel kanan: tim dan alat (kebutuhan)
with tb_tim:
    if res:
        st.markdown("**Seberapa cepat selesai bila ditambah?**")
        n_maks = st.slider("Sampai berapa tim", 2, 12, 6, key="rt_nmaks")
        kb = rt.kebutuhan_sumber_daya(res["M"], res["urut"][best], n_maks, (1, 2, 3), float(S["rt_alpha"]))
        pv = kb.pivot(index="tim", columns="alat_per_tim", values="selesai_jam")
        pv.columns = [f"{c} alat/tim" for c in pv.columns]
        st.line_chart(pv, height=200, x_label="jumlah tim", y_label="jam selesai semua")
        target = st.number_input("Target selesai (jam sejak hujan mulai)", 1.0, 2000.0, float(round(res["rg"][best]["selesai"] * 0.6 + 1)),
                                 1.0, key="rt_target")
        ok = kb[kb["selesai_jam"] <= target].sort_values(["total_alat", "tim"])
        if len(ok):
            r0 = ok.iloc[0]
            st.markdown(gaya.chip(f"🎯 Cukup {int(r0['tim'])} tim × {int(r0['alat_per_tim'])} alat berat ({int(r0['total_alat'])} alat) "
                                  f"→ selesai jam {r0['selesai_jam']:.0f}", "ok"), unsafe_allow_html=True)
        else:
            st.markdown(gaya.chip("🎯 Target belum tercapai walau 3 alat per tim; waktu tunggu air surut atau jarak mobilisasi membatasi.",
                                  "warn"), unsafe_allow_html=True)
        st.dataframe(kb.rename(columns={"alat_per_tim": "alat/tim", "total_alat": "total alat", "selesai_jam": "selesai (jam)",
                                        "t50_jam": "50% pulih", "t90_jam": "90% pulih", "luas_kerugian": "kerugian"}),
                     hide_index=True, width="stretch", height=200, column_config={
                         c: st.column_config.NumberColumn(format="%.1f") for c in ("selesai (jam)", "50% pulih", "90% pulih", "kerugian")})
        st.caption("Tabel memakai urutan terbaik saat ini, tim dasar saja (tanpa tim tambahan). Produktivitas tim = jumlah alat ^ efek tambahan alat: "
                   "asumsi, ganti dengan data lapangan.")
    else:
        st.info("Tandai ruas rusak dulu untuk melihat kebutuhan tim dan alat berat.")

# =============================================================== panel kanan: bagaimana jika
with tb_jika:
    st.caption("Coba intervensi lalu bandingkan dampaknya dengan simulasi utama: pompa, saluran dibersihkan (kapasitas drainase di area itu dikali), "
               "atau satu ruas ditinggikan. Tambah lewat tabel atau 📍 Tambah titik di peta.")
    iv_now = st.data_editor(
        S["rt_interv"], num_rows="dynamic", hide_index=True, width="stretch", key=f"rt_iv_{S['rt_iver']}",
        column_config={"jenis": st.column_config.SelectboxColumn(options=["pompa", "drainase", "tinggikan"]),
                       "lat": st.column_config.NumberColumn(format="%.6f"), "lon": st.column_config.NumberColumn(format="%.6f"),
                       "ruas": st.column_config.NumberColumn("nomor urut ruas", help="Hanya untuk 'tinggikan' (urutan baris di file jalan)"),
                       "nilai": st.column_config.NumberColumn(help="pompa: debit m³/s · drainase: pengali kapasitas · tinggikan: cm"),
                       "radius_m": st.column_config.NumberColumn("radius (m)")})
    S["rt_interv_now"] = iv_now
    iv_param = []
    for r_ in iv_now.itertuples():
        if r_.jenis == "tinggikan" and pd.notna(r_.ruas) and 1 <= int(r_.ruas) <= len(gdf):
            iv_param.append({"jenis": "tinggikan", "ruas": int(r_.ruas) - 1, "nilai": float(r_.nilai) if pd.notna(r_.nilai) else 40.0})
        elif r_.jenis in ("pompa", "drainase") and pd.notna(r_.lat) and pd.notna(r_.lon):
            x_, y_ = ll2utm(float(r_.lon), float(r_.lat))
            iv_param.append({"jenis": r_.jenis, "x": x_, "y": y_, "nilai": float(r_.nilai) if pd.notna(r_.nilai) else 2.0,
                             "radius_m": float(r_.radius_m) if pd.notna(r_.radius_m) else (60.0 if r_.jenis == "pompa" else 300.0)})
    if ctx is None:
        st.info("Butuh DEM dan simulasi.")
    elif st.button("▶️ Bandingkan dengan intervensi ini", key="rt_jika_run", disabled=not iv_param or job_aktif, type="primary"):
        pb = param_sim()
        pj = param_sim(intervensi=iv_param)
        S["rt_job"] = sk.Job("jika", sk.tugas_dari_param(ctx, [pb, pj], ["pembanding", "dengan intervensi"]), cache,
                             {"dasar": sk.kunci(ctx, pb), "jika": sk.kunci(ctx, pj)})
        st.rerun()
    jk = S.get("rt_jika")
    if jk and _ambil(jk["dasar"]) is not None and _ambil(jk["jika"]) is not None:
        a_, b_ = metrik(jk["dasar"], _ambil(jk["dasar"])), metrik(jk["jika"], _ambil(jk["jika"]))
        dfc = pd.DataFrame({"Tanpa intervensi": a_, "Dengan intervensi": b_})
        dfc["Selisih"] = dfc["Dengan intervensi"] - dfc["Tanpa intervensi"]
        st.dataframe(dfc, width="stretch", column_config={c: st.column_config.NumberColumn(format="%.1f") for c in dfc.columns})
        st.caption("Selisih negatif = lebih baik. Pilih hasilnya di Tab Simulasi (Hasil yang ditampilkan di peta) untuk melihat petanya, lalu 'Terapkan' "
                   "bila ingin menghitung perbaikan dari skenario itu.")

# =============================================================== 4. HASIL DAN UNDUHAN
kelas_marks = ["rusak" if i in P else "tergenang" if i in T else "lambat" if i in L else "normal" for i in range(len(gdf))]
with tab_hasil:
    st.markdown("### Peringkat ruas")
    tabel = hasil.sort_values("peringkat")
    topn = st.number_input("Tampilkan N ruas teratas", 1, len(tabel), min(25, len(tabel)), key="rt_topn")
    kolom_tabel = ["peringkat", "persentil_q", "ruas", "nama_jalan", "prob_banjir", "kelas", "skor", "yakin_nama_pct"]
    st.dataframe(tabel[kolom_tabel].head(int(topn)), hide_index=True, width="stretch", column_config={
        "prob_banjir": st.column_config.ProgressColumn("prob. banjir" + (" (dari tinggi simulasi)" if pakai_sim else " (fl_max)"),
                                                       min_value=0.0, max_value=1.0, format="%.2f"),
        "skor": st.column_config.NumberColumn("skor", format="%.3f"),
        "persentil_q": st.column_config.NumberColumn("q = r/N", format="%.3f", help="Persentil kumulatif peringkat"),
        "yakin_nama_pct": st.column_config.NumberColumn("keyakinan nama (%)", format="%.0f",
                                                        help="Seberapa konsisten nama jalan OSM sepanjang ruas ini")})
    g1, g2 = st.columns(2)
    g1.download_button("⬇️ Tabel ruas (CSV)", tabel.to_csv(index=False).encode("utf-8"), "krusialitas_ruas.csv", "text/csv")
    g2.download_button("⬇️ Peta ruas (GeoJSON)", g.to_json().encode("utf-8"), "krusialitas_ruas.geojson")

    if sr is not None:
        st.markdown(f"### Banjir per ruas ({pilih_sim})")
        wk2 = sk.waktu_per_ruas(sr, amb)
        per_ruas = pd.DataFrame({
            "ruas": no_ruas.values, "nama_jalan": jalan.values, "tinggi_banjir_maks_m": sr["dmaks"].round(3),
            "tinggi_banjir_p90_m": sr["dp90"].round(3), "fl_max": sim.prob_dari_kedalaman(sr["dmaks"], S["rt_penuh"]).round(3),
            "status": sk.hitung_kelas(sr, amb), "mulai_putus_jam": wk2["putus_mulai"].round(2), "terbuka_lagi_jam": wk2["putus_selesai"].round(2),
            "lama_putus_jam": wk2["putus_lama"].round(2), "lama_genangan_jam": wk2["genang_lama"].round(2),
            "belum_surut": wk2["belum_surut"]})
        kunci_id = idc if idc != "(nomor urut)" else "no_ruas"
        per_ruas.insert(0, kunci_id, gdf[idc].values if idc != "(nomor urut)" else (gdf.index + 1).values)
        tampil_r = per_ruas[per_ruas["status"] != "normal"].sort_values("tinggi_banjir_maks_m", ascending=False)
        st.dataframe(tampil_r, hide_index=True, width="stretch", height=300)
        st.download_button("⬇️ Banjir per ruas (CSV)", per_ruas.to_csv(index=False).encode("utf-8"), "simulasi_banjir_ruas.csv", "text/csv",
                           help="Bisa diunggah kembali: kolom fl_max baru menggantikan fl_max di jalan lewat kolom nomor ruas.")
        st.caption("Mulai putus dan terbuka lagi dihitung dari saat tinggi air ≥ ambang tergenang. Ruas rusak tetap putus sesudah air surut "
                   "sampai diperbaiki.")

    skn = S.get("rt_skn")
    daftar = [(float(mm_), _ambil(k_)) for mm_, k_ in (skn or {}).get("niveau", {}).items() if _ambil(k_) is not None]
    if len(daftar) >= 3:
        st.markdown("### Ambang hujan per ruas")
        ah = sk.ambang_hujan(daftar, amb)
        ah.insert(0, "ruas", no_ruas.values)
        ah.insert(1, "nama_jalan", jalan.values)
        ah = ah.rename(columns={"hujan_terganggu_mm": "terganggu mulai (mm)", "hujan_tergenang_mm": "tergenang mulai (mm)",
                                "hujan_rusak_mm": "rusak mulai (mm)"})
        ah = ah[ah[["terganggu mulai (mm)", "tergenang mulai (mm)", "rusak mulai (mm)"]].notna().any(axis=1)]
        ah = ah.sort_values(["tergenang mulai (mm)", "terganggu mulai (mm)"], na_position="last")
        st.dataframe(ah.head(40), hide_index=True, width="stretch", height=300, column_config={
            c: st.column_config.NumberColumn(format="%.0f") for c in ah.columns if c.endswith("(mm)")})
        st.caption(f"Hujan minimum (mm) yang membuat ruas mencapai tiap kelas, diinterpolasi dari skenario {min(m for m, _ in daftar):g}–"
                   f"{max(m for m, _ in daftar):g} mm. Kosong = tidak tercapai sampai hujan tertinggi. Urut dari ruas yang putus paling dulu: "
                   "itu yang diutamakan untuk persiapan.")
        st.download_button("⬇️ Ambang hujan (CSV)", ah.to_csv(index=False).encode("utf-8"), "ambang_hujan_ruas.csv", "text/csv")

    st.markdown("### Ekspor ke QGIS dan ringkasan")
    if sr is not None:
        if st.button("📦 Siapkan GeoTIFF kedalaman dan poligon genangan", key="rt_exp_btn"):
            with st.spinner("Menyiapkan berkas..."):
                S["rt_exp"] = {"key": sr_key,
                               "tif": ek.geotiff_bytes(sr["hs"]["h_maks"], sr["dx"], sr["asal"], utm_crs),
                               "poly": ek.poligon_genangan(sr["hs"]["h_maks"], sr["dx"], sr["asal"], utm_crs).to_json().encode("utf-8")}
        ex = S.get("rt_exp")
        if ex and ex["key"] == sr_key:
            x1_, x2_ = st.columns(2)
            x1_.download_button("⬇️ Kedalaman maksimum (GeoTIFF)", ex["tif"], "kedalaman_maks.tif", "image/tiff")
            x2_.download_button("⬇️ Poligon genangan (GeoJSON)", ex["poly"], "genangan.geojson")
            st.caption("GeoTIFF dalam CRS UTM data Anda, satuan meter, sel kering = nodata. Poligon dikelompokkan <15, 15–30, 30–60, >60 cm (EPSG:4326).")
    else:
        st.caption("Jalankan simulasi untuk mengekspor kedalaman maksimum dan poligon genangan.")
    if st.button("📄 Buat ringkasan satu halaman (PDF)", key="rt_pdf_btn"):
        kpi = []
        if sr is not None:
            mk_ = metrik(sr_key, sr)
            kpi += [("Ruas rusak", mk_["Rusak"]), ("Ruas tergenang", mk_["Tergenang"]), ("Ruas terganggu", mk_["Terganggu"]),
                    ("Tinggi air maks (m)", f"{mk_['Tinggi maks (m)']:.2f}")]
            if not np.isnan(mk_["Penduduk terdampak"]):
                kpi.append(("Penduduk terdampak", f"{mk_['Penduduk terdampak']:,.0f}"))
            if not np.isnan(mk_["Terisolasi dari RS, puncak (%)"]):
                kpi.append(("Terisolasi dari RS (puncak)", f"{mk_['Terisolasi dari RS, puncak (%)']:.0f}%"))
        else:
            kpi += [("Ruas rusak", len(P)), ("Ruas tergenang", len(T)), ("Ruas terganggu", len(L))]
        if res:
            kpi.append(("Perbaikan selesai", gaya.jam_teks(res["rg"][best]["selesai"])))
            kpi.append(("Tim / alat berat", f"{len(TIM)} / {sum(t_['alat'] for t_ in TIM):.0f}"))
        sk_ = {}
        if sr is not None:
            p_ = sr["p"]
            sk_ = {"Hujan": f"{p_['hujan']:.0f} mm / {p_['durasi']:g} jam", "Pola": POLA_LBL.get("csv" if p_.get("hujan_tabel") else p_.get("pola"), ""),
                   "CN": f"{p_['cn']:.0f}", "Tanah": p_["amc"], "Drainase": f"{p_['drainase']:g} mm/jam ({p_['kondisi_drainase']})"}
        tx_, ty_ = ll2utm(tv["lon"].to_numpy(), tv["lat"].to_numpy()) if len(tv) else ([], [])
        urut_pdf = [(int(r.urutan), nama[int(r.ruas)], r.mulai_jam, r.selesai_jam, r.tim_nama) for r in jd.sort_values("urutan").itertuples()] if res else []
        S["rt_pdf"] = ek.ringkasan_pdf({
            "judul": "Ringkasan banjir dan penanganan jalan", "skenario": sk_, "kpi": kpi, "geoms": list(S["rt_utm"].geometry),
            "kelas": list(sk.hitung_kelas(sr, amb)) if sr is not None and S.get("rt_asal_sim") == sr_key else kelas_marks,
            "titik": list(zip(tx_, ty_, tv["jenis"])), "urutan": urut_pdf,
            "catatan": ["Ruas rusak = banjir cukup dalam sehingga membawa sedimen dan menggerus jalan; tetap putus sesudah air surut dan perlu "
                        "diperbaiki. Ruas tergenang = putus sementara dan terbuka sendiri saat air surut lewat drainase.",
                        "Jam dihitung sejak hujan mulai. Perbaikan ruas rusak baru dimulai setelah air di ruas itu surut.",
                        "Kecepatan jalan, durasi perbaikan, produktivitas alat berat, dan CN adalah asumsi; sesuaikan dengan data BPBD/PU dan kalibrasi "
                        "dengan jejak banjir."]})
    if S.get("rt_pdf"):
        st.download_button("⬇️ Ringkasan (PDF)", S["rt_pdf"], "ringkasan_jalan_siaga.pdf", "application/pdf")

    st.markdown("""
**Kelas kekritisan (bawaan):** dari persentil peringkat skor, q = r/N: Sangat kritis (merah) 10% teratas, Tinggi (oranye) 20% berikutnya, Sedang (kuning) 30% berikutnya, Rendah (hijau) 40% terbawah. Pembagian ini relatif, bukan ambang bahaya absolut; ganti ke ambang nilai di Pengaturan rinci bila perlu.

**Warna jalan (mode ambang nilai):** bila simulasi dipakai, dari tinggi air maksimum di ruas (hijau < ambang terganggu, kuning sampai ambang tergenang, merah di atasnya).
Bila tidak, dari probabilitas banjir `fl_max`. Batas bisa diubah di Pengaturan rinci.

**Tergenang atau rusak:** ruas tergenang putus sementara dan terbuka sendiri saat air surut karena drainase. Ruas dengan air di atas ambang rusak
dianggap membawa sedimen dan menggerus jalan, sehingga tetap putus dan masuk antrean perbaikan setelah airnya surut.

**Urutan perbaikan:** tiap langkah memilih ruas yang paling menurunkan kerugian per jam kerja (greedy), lalu diberikan ke tim yang paling cepat
menyelesaikannya (alat berat mempercepat tim, tim bantuan boleh tiba belakangan). Kerugian menggabungkan akses ke RS/posko, akses alat berat,
waktu tempuh, keterhubungan jaringan, dan probabilitas banjir.

**Rujukan:** Bates, Horritt & Fewtrell (2010), *A simple inertial formulation of the shallow water equations for efficient two-dimensional flood
inundation modelling*, J. Hydrol. 387; USDA-SCS (1985/1986), *National Engineering Handbook Sec. 4* dan TR-55 (Curve Number dan AMC);
Gorani & Dong, *Optimization of Equitable Routes to Critical Facilities After Flood Events*; Moghtadernejad, Adey & Hackl (2022),
*Determination of Postdisaster Restoration Programs for Road Networks Using a Double-Stage Optimization Approach*, J. Infrastruct. Syst.
(doi:10.1061/(ASCE)IS.1943-555X.0000700); *Long-term scheduling for road network disaster recovery*, IJDRR 42 (2020) 101353;
*Restoration of services in disrupted infrastructure systems: A network science approach* (PMC5812613).

**Batasan:** kecepatan jalan, durasi perbaikan, kapasitas drainase, produktivitas alat, dan ambang tinggi adalah asumsi, bukan angka dari jurnal.
Model genangan adalah skrining (bukan HEC-RAS 2D); sungai dimodelkan sebagai debit masuk, bukan penampang saluran. Waktu tempuh tanpa
kemacetan dan kapasitas jalan.
""")

# =============================================================== nama jalan (manual) dan simpan proyek, di tab Data
with tab_data:
    with st.expander("🔎 Nama jalan dari OpenStreetMap (opsional)"):
        st.caption("Dicocokkan lewat posisi dengan jalan OSM terdekat (≤25 m, butuh internet). Hanya berjalan saat tombol ditekan, hasilnya "
                   "tersimpan di proyek. Sesekali bisa keliru di persimpangan.")
        if st.button("🔎 Cari nama jalan", key="rt_cari", disabled=demo):
            bar = st.progress(0.0, text="Mengunduh nama jalan dari OpenStreetMap...")
            try:
                hasil_nj, cat_nj = nj.cari_nama(gdf, S["rt_utm"], progres=lambda i, n: bar.progress(
                    min(i / max(n, 1), 1.0), text=f"Mengunduh peta OSM ({i + 1}/{n})..."))
                S["rt_nj"], S["rt_njcat"], S["rt_njgalat"] = hasil_nj, cat_nj, None
            except Exception as e:
                S["rt_njgalat"] = str(e)
            bar.empty()
            st.rerun()
        if S.get("rt_njgalat"):
            st.warning(f"Nama jalan belum bisa dicari: {S['rt_njgalat']} Tabel tetap memakai nomor ruas.")
        elif S.get("rt_njcat"):
            st.caption(S["rt_njcat"])

    with st.expander("💾 Simpan proyek"):
        st.caption("Menyimpan tanda ruas, titik, masukan, intervensi, nama jalan, dan semua pengaturan. Unggah file ini bersama jalan yang sama "
                   "untuk melanjutkan; berkas jalan dan DEM tidak ikut tersimpan.")
        nj_json = None
        if S.get("rt_nj") is not None:
            d_ = S["rt_nj"].copy()
            d_.index.name = "i"
            nj_json = json.loads(d_.reset_index().to_json(orient="records"))
        recs = lambda df: json.loads(df.to_json(orient="records"))
        proyek = {"jalan_siaga": 1, "sig_jalan": {"n": len(gdf)},
                  "status": {"putus": sorted(map(int, S["rt_putus"])), "lambat": sorted(map(int, S["rt_lambat"])),
                             "tergenang": sorted(map(int, S["rt_tergenang"]))},
                  "rilis": {str(k): v for k, v in S["rt_rilis"].items()}, "twin": {str(k): list(v) for k, v in S["rt_twin"].items()},
                  "tdur": {str(k): v for k, v in S["rt_tdur"].items()}, "titik": recs(titik_now),
                  "sungai": recs(S["rt_sungai_now"]), "interv": recs(S.get("rt_interv_now", S["rt_interv"])),
                  "param": {k: S[k] for k in PARAM_KEYS if k in S}, "nama_jalan": nj_json}
        st.download_button("⬇️ Simpan proyek (.json)", json.dumps(proyek, default=str).encode("utf-8"), "proyek_jalan_siaga.json",
                           "application/json")

# =============================================================== panel tugas latar belakang (di atas, selalu terlihat)
JUDUL_JOB = {"utama": "Simulasi banjir", "skenario": "Skenario hujan", "kalibrasi": "Kalibrasi CN", "jika": "Bagaimana jika"}


def _panel_job():
    j = S.get("rt_job")
    if j is None:
        return
    if j.status != "berjalan":
        if not j.diproses:
            st.rerun()
        return
    sisa = j.sisa_detik()
    st.progress(min(j.frac, 1.0), text=f"⏳ {JUDUL_JOB.get(j.jenis, 'Tugas')}: simulasi {min(j.ke + 1, j.n)} dari {j.n}"
                + (f" · sisa ±{sisa:.0f} detik" if sisa is not None else " · menghitung perkiraan..."))
    if st.button("⏹ Batalkan", key="rt_batal"):
        j.batal()


with c_job:
    jb = S.get("rt_job")
    if jb is not None and jb.status == "berjalan":
        st.fragment(run_every=1.0)(_panel_job)()
