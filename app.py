import os
import re
import tempfile
import zipfile

import folium
import geopandas as gpd
import numpy as np
import pandas as pd
import streamlit as st
from shapely.geometry import LineString
from streamlit_folium import st_folium

import scoring as sc

st.set_page_config(page_title="Krusialitas Jaringan Jalan", layout="wide")


def tebak(n):
    n = n.lower()
    if re.search(r"status|fungsi|kelas|hierarki", n): return "status"
    if re.search(r"dens|kepadatan|permukiman|pemukiman|penduduk", n): return "dens"
    if re.search(r"(^|_)rs(_|$)|sakit|faskes|fasilitas|antar|konek|akses|jarak|dist", n): return "konek"
    if re.search(r"banjir|longsor|gempa|likuef|tsunami|erupsi|kering|angin|prob|^p_|_p$|hazard|bahaya|rawan|_max$", n): return "haz"
    if re.search(r"^(id|nama|name|ruas)", n): return "id"
    return ""


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


def contoh():
    rng = np.random.default_rng(7)
    nx, ny = 9, 6
    pts = [(107.72 + i * .012 + rng.random() * .004, -7.08 + j * .01 + rng.random() * .003)
           for j in range(ny) for i in range(nx)]
    rows = []
    for j in range(ny):
        for i in range(nx):
            a = j * nx + i
            for b in ([a + 1] if i < nx - 1 else []) + ([a + nx] if j < ny - 1 else []):
                d = ((pts[a][1] + pts[b][1]) / 2 + 7.08) / .05
                rows.append({"id_ruas": f"R-{len(rows) + 1:03d}",
                             "fungsi": str(rng.choice(["Arteri", "Kolektor", "Lokal", "Lokal", "Lingkungan"])),
                             "p_banjir": round(float(np.clip(.75 - d * .6 + rng.random() * .3, 0, 1)), 2),
                             "p_longsor": round(float(rng.random() * .4), 2),
                             "p_gempa": round(float(.1 + rng.random() * .2), 2),
                             "n_konek": int(rng.integers(1, 6)),
                             "dens_km": round(float(20 + rng.random() * 180), 1),
                             "geometry": LineString([pts[a], pts[b]])})
    return gpd.GeoDataFrame(rows, crs=4326)


# ---------------- Sidebar: data ----------------
with st.sidebar:
    st.header("1. Data")
    f = st.file_uploader("Jaringan jalan (GeoJSON, GeoPackage, atau Shapefile .zip)",
                         type=["geojson", "json", "gpkg", "zip"])
    if st.button("Pakai data contoh"):
        st.session_state["demo"] = True
    t = st.file_uploader("Tabel tambahan (Excel/CSV), opsional", type=["xlsx", "csv"],
                         help="Dipakai kalau atribut ada di Excel dan peta dari file terpisah.")

st.title("Krusialitas Jaringan Jalan")
if f is not None:
    gdf = baca_geo(f.name, f.getvalue())
elif st.session_state.get("demo"):
    gdf = contoh()
else:
    st.info("Unggah file jaringan jalan di panel kiri, atau klik **Pakai data contoh**.")
    st.stop()

if gdf.crs is None:
    epsg = st.sidebar.number_input("CRS file tidak terbaca. Kode EPSG:", value=32749, step=1)
    gdf = gdf.set_crs(int(epsg))
gdf = gdf.to_crs(4326)                      # peta selalu butuh lat/lon
gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].reset_index(drop=True)
if not gdf.geom_type.str.contains("Line").all():
    st.warning("Ada geometri bukan garis. Hanya ruas jalan (garis) yang sebaiknya diunggah.")

if t is not None:
    tb = pd.read_excel(t) if t.name.lower().endswith("xlsx") else pd.read_csv(t, sep=None, engine="python")
    with st.sidebar:
        kg = st.selectbox("Kolom kunci di peta", [c for c in gdf.columns if c != "geometry"])
        kt = st.selectbox("Kolom kunci di tabel", tb.columns)
    gdf = gdf.merge(tb.drop_duplicates(kt), left_on=kg, right_on=kt, how="left", suffixes=("", "_tabel"))

# ---------------- Sidebar: kolom & metode ----------------
kol = [c for c in gdf.columns if c != "geometry"]
pil = lambda rol: [c for c in kol if tebak(c) == rol]
with st.sidebar:
    st.header("2. Peran kolom")
    idc = st.selectbox("ID / nama ruas", ["(nomor urut)"] + kol,
                       index=(kol.index(pil("id")[0]) + 1) if pil("id") else 0)
    haz = st.multiselect("Bahaya (probabilitas/indeks tiap bencana)", kol, default=pil("haz"))
    st_col = st.selectbox("Status jalan", ["(tidak ada)"] + kol,
                          index=(kol.index(pil("status")[0]) + 1) if pil("status") else 0)
    konek = st.multiselect("Konektivitas", kol, default=pil("konek"))
    dens = st.multiselect("Densitas permukiman", kol, default=pil("dens"))

    st.header("3. Metode")
    how = st.selectbox("Gabungan bahaya", ["union", "max", "mean"], format_func={
        "union": "Peluang salah satu terjadi, 1−Π(1−p)", "max": "Maksimum", "mean": "Rata-rata terbobot"}.get)
    mode = st.radio("Rumus skor", ["mul", "add"], format_func={
        "mul": "Risiko = Bahaya × Konsekuensi", "add": "Jumlah terbobot"}.get)
    norm = st.checkbox("Normalisasi min–max tiap bahaya", False,
                       help="Samakan rentang antar bencana. Hati-hati: bencana dengan rentang sempit jadi terlalu dominan.")
    fill_min = st.checkbox("Isi data kosong dengan nilai minimum kolom", True)
    inv = st.checkbox("Konektivitas berupa jarak (kecil = baik)", False)
    wh = st.slider("Bobot bahaya", 0, 10, 5)
    ws = st.slider("Bobot status jalan", 0, 10, 3)
    wk = st.slider("Bobot konektivitas", 0, 10, 4)
    wd = st.slider("Bobot densitas", 0, 10, 3)
    with st.expander("Nilai status jalan (0–1)"):
        nilai = {k: st.number_input(k.capitalize(), 0.0, 1.0, v, 0.1)
                 for k, v in {"arteri": 1.0, "kolektor": 0.7, "lokal": 0.4, "lainnya": 0.2}.items()}
    bobot_haz = {}
    if how == "mean" and haz:
        with st.expander("Bobot tiap bencana"):
            bobot_haz = {c: st.number_input(c, 0.0, 10.0, 1.0, 0.5) for c in haz}

if not haz:
    st.warning("Pilih minimal satu kolom bahaya di panel kiri.")
    st.stop()

# ---------------- Hitung ----------------
nol = pd.Series(0.0, index=gdf.index)
H = sc.hazard(gdf, haz, how, bobot_haz, norm, fill_min)
S = sc.status(gdf[st_col], nilai) if st_col != "(tidak ada)" else nol
K = sc.rata_minmax(gdf, konek, inv) if konek else nol
D = sc.rata_minmax(gdf, dens) if dens else nol
w = {"h": wh, "s": ws if st_col != "(tidak ada)" else 0, "k": wk if konek else 0, "d": wd if dens else 0}
hasil = sc.hitung(H, S, K, D, w, mode)
hasil.insert(0, "ruas", (gdf[idc].astype(str) if idc != "(nomor urut)" else (gdf.index + 1).astype(str)))
hasil["bahaya"], hasil["status"], hasil["konektivitas"], hasil["densitas"] = H, S, K, D
g = gdf[["geometry"]].join(hasil)

# ---------------- Tampilan ----------------
tab_peta, tab_tabel, tab_metode = st.tabs(["Peta", "Tabel peringkat", "Metode"])

with tab_peta:
    c = st.columns(4)
    c[0].metric("Ruas dianalisis", len(g))
    for col, k in zip(c[1:], sc.KELAS[:3]):
        col.metric(k, int((g.kelas == k).sum()))
    b = g.total_bounds
    m = folium.Map(tiles="OpenStreetMap", control_scale=True)
    folium.TileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
                     attr="Esri", name="Citra satelit").add_to(m)
    m.fit_bounds([[b[1], b[0]], [b[3], b[2]]])
    for k in reversed(sc.KELAS):            # kelas rendah digambar dulu, kritis di atas
        sub = g[g.kelas == k][["ruas", "kelas", "skor", "geometry"]].copy()
        if sub.empty:
            continue
        sub["skor"] = sub["skor"].round(3)
        folium.GeoJson(
            sub, name=k,
            style_function=lambda _, c=sc.WARNA[k], l=sc.LEBAR[k]: {"color": c, "weight": l, "opacity": .95},
            tooltip=folium.GeoJsonTooltip(fields=["ruas", "kelas", "skor"], aliases=["Ruas", "Kelas", "Skor"]),
        ).add_to(m)
    folium.LayerControl(collapsed=True).add_to(m)
    st_folium(m, height=580, use_container_width=True, returned_objects=[])
    st.markdown(" &nbsp; ".join(f"<span style='color:{sc.WARNA[k]}'>■</span> {k}" for k in sc.KELAS),
                unsafe_allow_html=True)

with tab_tabel:
    tabel = hasil.sort_values("peringkat")
    n = st.number_input("Tampilkan N teratas", 1, len(tabel), min(25, len(tabel)))
    st.dataframe(tabel.head(int(n)), hide_index=True, width="stretch", column_config={
        "skor": st.column_config.ProgressColumn("skor", min_value=0.0, max_value=float(max(tabel.skor.max(), 1e-9)), format="%.3f"),
        **{k: st.column_config.NumberColumn(format="%.2f") for k in ["bahaya", "status", "konektivitas", "densitas"]}})
    d1, d2 = st.columns(2)
    d1.download_button("Unduh tabel (CSV)", tabel.to_csv(index=False).encode("utf-8"), "krusialitas_ruas.csv", "text/csv")
    d2.download_button("Unduh peta hasil (GeoJSON)", g.to_json().encode("utf-8"), "krusialitas_ruas.geojson")

with tab_metode:
    st.markdown("""
Setiap komponen dinormalkan ke 0–1.

- **Bahaya (H)**: gabungan kolom bahaya per ruas (1−Π(1−p), maksimum, atau rata-rata terbobot). Nilai 0–100 dianggap persen.
- **Status (S)**: arteri, kolektor, lokal, lainnya sesuai nilai di panel. Kolom angka dinormalkan min–max.
- **Konektivitas (K)** dan **Densitas (D)**: rata-rata min–max kolom yang dipilih.
- **Risiko = H × (ws·S + wk·K + wd·D) / (ws+wk+wd)**, atau **jumlah terbobot** (wh·H′ + ws·S + wk·K + wd·D) / Σw.
- **Kelas** berdasarkan peringkat: 10% teratas Sangat kritis, 20% berikutnya Tinggi, 30% Sedang, sisanya Rendah.
""")
