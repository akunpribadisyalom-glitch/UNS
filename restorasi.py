"""Simulasi gangguan jaringan jalan dan penyusunan urutan perbaikan pascabencana.

Tanpa ketergantungan Streamlit. Satuan: waktu tempuh di jaringan dalam detik,
durasi perbaikan dan jadwal dalam jam.

Gambaran model
--------------
* Jaringan jalan -> graf (ujung ruas yang berimpit menjadi simpul).
* Gangguan: ruas "putus" (tidak bisa dilewati sampai diperbaiki) dan ruas
  "terganggu" (bisa dilewati tetapi lebih lambat, mis. tergenang).
* Kerugian L(S) untuk himpunan ruas yang sudah diperbaiki S adalah rata-rata
  terbobot dari beberapa metrik yang sudah dinormalkan 0 (semua pulih) .. 1 (semua putus):
  akses ke RS, akses ke posko, akses alat berat, keterhubungan, waktu tempuh
  perjalanan (OD), dan krusialitas ruas. Bobotnya bergantung pada jenis bencana.
* Urutan perbaikan dicari agar luas di bawah kurva kerugian terhadap waktu sekecil mungkin.
"""
import math
import random
import time

import networkx as nx
import numpy as np
import pandas as pd

import scoring as sc

JENIS_TITIK = ["rs", "posko", "depot", "permukiman"]

KECEPATAN = {"arteri": 40.0, "kolektor": 30.0, "lokal": 20.0, "lainnya": 15.0}   # km/jam, asumsi
# (jam tetap per ruas, jam tambahan per km): ASUMSI awal, ganti dengan data BPBD/PU setempat.
PERBAIKAN = {"banjir": (3.0, 6.0), "longsor": (8.0, 24.0), "gempa": (12.0, 30.0), "umum": (6.0, 12.0)}
HAMBATAN = {"banjir": 0.3, "longsor": 0.5, "gempa": 0.5, "umum": 0.5}               # faktor kecepatan ruas terganggu
PERLU_DEPOT = {"banjir": False, "longsor": True, "gempa": True, "umum": False}

# Bobot metrik per jenis bencana (operasionalisasi temuan literatur; bukan angka dari jurnal).
PROFIL = {
    "banjir":  {"rs": .40, "posko": .25, "depot": .00, "konek": .10, "od": .15, "krus": .10},
    "longsor": {"rs": .20, "posko": .10, "depot": .35, "konek": .15, "od": .05, "krus": .15},
    "gempa":   {"rs": .30, "posko": .15, "depot": .20, "konek": .15, "od": .05, "krus": .15},
    "umum":    {"rs": .20, "posko": .15, "depot": .00, "konek": .20, "od": .30, "krus": .15},
}


# ---------------------------------------------------------------- graf
def kelas_jalan(s):
    """Teks status jalan -> arteri / kolektor / lokal / lainnya."""
    def f(x):
        x = str(x).lower()
        for k, kata in sc.KATA_STATUS.items():
            if any(w in x for w in kata):
                return k
        return "lainnya"
    return s.map(f)


def _n(c, d):
    return (round(float(c[0]), d), round(float(c[1]), d))


def bangun_graf(g, kelas, kecepatan, desimal=5):
    """GeoDataFrame garis (EPSG:4326) -> nx.Graph. Atribut sisi: i (posisi baris), t (detik), l (meter).

    Ujung ruas disatukan bila sama setelah dibulatkan ke `desimal` angka derajat
    (5 desimal ~ 1 m). Persimpangan di tengah ruas TIDAK dipecah otomatis.
    """
    g = g.reset_index(drop=True)
    panjang = g.to_crs(g.estimate_utm_crs()).length.to_numpy()
    G = nx.Graph()
    ekstra = 0
    for i, (geom, kls) in enumerate(zip(g.geometry, list(kelas))):
        bagian = list(geom.geoms) if geom.geom_type == "MultiLineString" else [geom]
        tot = sum(b.length for b in bagian) or 1.0
        v = kecepatan.get(kls, kecepatan["lainnya"]) * 1000 / 3600
        for b in bagian:
            if b.geom_type != "LineString" or len(b.coords) < 2:
                continue
            a, z = _n(b.coords[0], desimal), _n(b.coords[-1], desimal)
            if a == z:
                continue
            l = float(panjang[i] * b.length / tot)
            t = max(l, 1.0) / v
            if G.has_edge(a, z):                 # ruas paralel: sisipkan simpul bantu
                m = ("m", i, ekstra)
                ekstra += 1
                G.add_edge(a, m, i=i, t=t / 2, l=l / 2)
                G.add_edge(m, z, i=i, t=t / 2, l=l / 2)
            else:
                G.add_edge(a, z, i=i, t=t, l=l)
    if G.number_of_edges() == 0:
        raise ValueError("Tidak ada ruas garis yang bisa dijadikan graf.")
    nodes = [n for n in G.nodes if isinstance(n[0], float)]
    G.graph["nodes"] = nodes
    G.graph["arr"] = np.array(nodes, dtype=float)
    G.graph["panjang"] = panjang
    return G


def titik_terdekat(G, lon, lat):
    """Simpul graf terdekat dari (lon, lat) dan jaraknya (m)."""
    arr = G.graph["arr"]
    dx = (arr[:, 0] - lon) * 111320 * math.cos(math.radians(lat))
    dy = (arr[:, 1] - lat) * 110540
    d2 = dx * dx + dy * dy
    j = int(np.argmin(d2))
    return G.graph["nodes"][j], float(math.sqrt(d2[j]))


def pasang_titik(G, df):
    """df kolom nama, jenis, lat, lon, bobot -> tambah kolom node dan jarak_snap_m."""
    d = df.copy()
    for c, isi in (("nama", ""), ("bobot", 1.0)):
        if c not in d.columns:
            d[c] = isi
    d["jenis"] = d["jenis"].astype(str).str.lower().str.strip()
    d = d[d["jenis"].isin(JENIS_TITIK)].copy()
    d["lat"] = pd.to_numeric(d["lat"], errors="coerce")
    d["lon"] = pd.to_numeric(d["lon"], errors="coerce")
    d["bobot"] = pd.to_numeric(d["bobot"], errors="coerce").fillna(1.0)
    d = d.dropna(subset=["lat", "lon"]).reset_index(drop=True)
    h = [titik_terdekat(G, lo, la) for lo, la in zip(d["lon"], d["lat"])]
    d["node"] = pd.Series([x[0] for x in h], dtype=object, index=d.index)
    d["jarak_snap_m"] = [x[1] for x in h]
    return d


# ---------------------------------------------------------------- perjalanan (OD)
def od_otomatis(G, perm, rs_nodes, rs_nama=None, maks=50):
    """Tiap permukiman -> RS terdekat menurut waktu normal. perm = [(nama, node, bobot)]."""
    rs_nama = rs_nama or {}
    od = []
    for nama, n, b in perm[:maks]:
        d = nx.single_source_dijkstra_path_length(G, n, weight="t")
        kand = [(d[r], r) for r in rs_nodes if r in d]
        tujuan = min(kand, key=lambda x: x[0])[1] if kand else next(iter(rs_nodes))
        od.append((f"{nama} → {rs_nama.get(tujuan, 'RS')}", n, tujuan, float(b)))
    return od


def od_dari_tabel(G, t):
    """Tabel: nama, asal_lat, asal_lon, tujuan_lat, tujuan_lon, [waktu_menit], [bobot]."""
    t = t.rename(columns=lambda c: str(c).lower().strip())
    kurang = [c for c in ("asal_lat", "asal_lon", "tujuan_lat", "tujuan_lon") if c not in t.columns]
    if kurang:
        raise ValueError("Kolom perjalanan kurang: " + ", ".join(kurang))
    od, obs = [], []
    for n, (_, r) in enumerate(t.iterrows()):
        nama = str(r["nama"]) if "nama" in t.columns and pd.notna(r["nama"]) else f"Perjalanan {n + 1}"
        o, _ = titik_terdekat(G, float(r["asal_lon"]), float(r["asal_lat"]))
        d, _ = titik_terdekat(G, float(r["tujuan_lon"]), float(r["tujuan_lat"]))
        b = float(r["bobot"]) if "bobot" in t.columns and pd.notna(r["bobot"]) else 1.0
        od.append((nama, o, d, b))
        obs.append(float(r["waktu_menit"]) if "waktu_menit" in t.columns and pd.notna(r["waktu_menit"]) else np.nan)
    return od, obs


# ---------------------------------------------------------------- model
class Model:
    def __init__(self, G, putus, lambat=None, rs=(), posko=(), depot=(), pop=None, od=(),
                 bobot=None, perlu_depot=False, penalti=7200.0, dur=None, krus=None):
        self.G = G
        self.putus = frozenset(int(i) for i in putus)
        self.lambat = {int(i): float(v) for i, v in (lambat or {}).items()}
        self.rs, self.posko, self.depot = set(rs), set(posko), set(depot)
        self.pop = dict(pop or {})
        self.od = list(od)
        self.w = dict(bobot or {})
        self.perlu_depot = bool(perlu_depot and self.depot)
        self.pen = float(penalti)
        dur = dur or {}
        self.dur = {i: max(float(dur.get(i, 1.0)), 0.05) for i in self.putus}
        self.krus = {int(k): float(v) for k, v in (krus or {}).items()}
        self.ujung = {}
        for u, v, d in G.edges(data=True):
            self.ujung.setdefault(d["i"], set()).update((u, v))
        self._memo = {}
        self._ref = None

    def _fn(self, blok, pakai_lambat=True):
        lam = self.lambat if pakai_lambat else {}

        def f(u, v, d):
            i = d["i"]
            if i in blok:
                return None
            return d["t"] / lam.get(i, 1.0)
        return f

    def _mentah(self, rep):
        if rep in self._memo:
            return self._memo[rep]
        G, pen = self.G, self.pen
        blok = self.putus - rep
        f = self._fn(blok)
        m = {}
        tot_pop = sum(self.pop.values())
        for kunci, sumber in (("rs", self.rs), ("posko", self.posko)):
            if sumber and tot_pop > 0:
                d = nx.multi_source_dijkstra_path_length(G, sumber, weight=f)
                m[kunci] = sum(b * min(d.get(n, pen), pen) for n, b in self.pop.items()) / tot_pop
        layak = set(blok)
        if self.depot:
            d = nx.multi_source_dijkstra_path_length(G, self.depot, weight=f)
            tak = {i for i in blok if not any(n in d for n in self.ujung[i])}
            if self.putus:
                m["depot"] = len(tak) / len(self.putus)
            if self.perlu_depot:
                layak = set(blok) - tak
        if self.od:
            per_o, tot, acc = {}, 0.0, 0.0
            for _, o, t, b in self.od:
                if o not in per_o:
                    per_o[o] = nx.single_source_dijkstra_path_length(G, o, weight=f)
                acc += b * min(per_o[o].get(t, pen), pen)
                tot += b
            if tot > 0:
                m["od"] = acc / tot
        H = nx.subgraph_view(G, filter_edge=lambda u, v: G[u][v]["i"] not in blok)
        besar = max((len(c) for c in nx.connected_components(H)), default=0)
        m["konek"] = 1 - besar / G.number_of_nodes()
        tk = sum(self.krus.get(i, 0.0) for i in self.putus)
        if tk > 0:
            m["krus"] = sum(self.krus.get(i, 0.0) for i in blok) / tk
        hasil = (m, frozenset(layak))
        self._memo[rep] = hasil
        return hasil

    def _referensi(self):
        if self._ref is None:
            base = self._mentah(self.putus)[0]
            worst = self._mentah(frozenset())[0]
            aktif = {k: self.w[k] for k in worst
                     if self.w.get(k, 0) > 0 and worst[k] - base.get(k, 0.0) > 1e-9}
            self._ref = (base, worst, aktif)
        return self._ref

    def hitung(self, rep):
        """Kerugian L (0 = semua pulih, 1 = tak ada yang diperbaiki) untuk himpunan `rep` yang sudah diperbaiki."""
        rep = frozenset(rep)
        m, layak = self._mentah(rep)
        base, worst, aktif = self._referensi()
        n = {k: min(max((m[k] - base[k]) / (worst[k] - base[k]), 0.0), 1.0) for k in aktif}
        sw = sum(aktif.values())
        L = sum(aktif[k] * n[k] for k in aktif) / sw if sw else 0.0
        return {"L": L, "norm": n, "mentah": m, "layak": layak}

    def luas(self, urut):
        """Luas kerugian untuk satu tim (berurutan): sum(durasi_i * L sebelum ruas ke-i selesai)."""
        rep, tot = frozenset(), 0.0
        for i in urut:
            tot += self.dur[i] * self.hitung(rep)["L"]
            rep = rep | {i}
        return tot

    def mobilisasi(self, i, rep):
        """Detik perjalanan dari depot terdekat ke ujung ruas i lewat jalan yang sudah terbuka."""
        if not self.depot:
            return 0.0
        d = nx.multi_source_dijkstra_path_length(self.G, self.depot, weight=self._fn(self.putus - rep))
        t = [d[n] for n in self.ujung[i] if n in d]
        return min(t) if t else 0.0


# ---------------------------------------------------------------- algoritma urutan
def dekode(M, prioritas):
    """Daftar prioritas -> urutan yang layak (tiap langkah ambil ruas layak pertama dalam daftar)."""
    rep, sisa, hasil = frozenset(), list(prioritas), []
    while sisa:
        lay = M.hitung(rep)["layak"]
        pilih = next((i for i in sisa if i in lay), sisa[0])   # tak ada yang layak -> akses darurat
        sisa.remove(pilih)
        hasil.append(pilih)
        rep = rep | {pilih}
    return hasil


def urut_greedy(M):
    """Tiap langkah: ruas dengan penurunan kerugian per jam kerja terbesar."""
    rep, sisa, urut = frozenset(), set(M.putus), []
    while sisa:
        info = M.hitung(rep)
        cand = [i for i in sisa if i in info["layak"]] or list(sisa)

        def skor(i):
            L1 = M.hitung(rep | {i})["L"]
            return ((info["L"] - L1) / M.dur[i], M.krus.get(i, 0.0), -M.dur[i])
        pilih = max(cand, key=skor)
        urut.append(pilih)
        sisa.remove(pilih)
        rep = rep | {pilih}
    return urut


def urut_sa(M, awal, iterasi=400, detik=30.0, seed=1):
    """Simulated annealing pada daftar prioritas, dimulai dari urutan `awal` (mis. hasil greedy)."""
    rng = random.Random(seed)
    n = len(awal)
    if n < 3:
        return list(awal)
    cur = list(awal)
    fc = M.luas(dekode(M, cur))
    best, fb = cur[:], fc
    T0 = max(fc * 0.1, 1e-9)
    t0 = time.time()
    for k in range(int(iterasi)):
        if time.time() - t0 > detik:
            break
        T = T0 * (1 - k / iterasi) + 1e-12
        cand = cur[:]
        a, b = rng.sample(range(n), 2)
        if rng.random() < .5:
            cand[a], cand[b] = cand[b], cand[a]
        else:
            cand.insert(b, cand.pop(a))
        fn = M.luas(dekode(M, cand))
        if fn < fc or rng.random() < math.exp(-(fn - fc) / T):
            cur, fc = cand, fn
            if fc < fb:
                best, fb = cur[:], fc
    return dekode(M, best)


def urut_optimal(M, maks=14):
    """Optimal tepat (pemrograman dinamis atas himpunan bagian) untuk satu tim; maksimal `maks` ruas putus."""
    ids = sorted(M.putus)
    n = len(ids)
    if n > maks:
        raise ValueError(f"Algoritma optimal hanya untuk maksimal {maks} ruas putus (sekarang {n}).")
    f = {frozenset(): (0.0, None)}
    lapis = [frozenset()]
    for _ in range(n):
        baru = {}
        for S in lapis:
            info = M.hitung(S)
            sisa = [i for i in ids if i not in S]
            lay = [i for i in sisa if i in info["layak"]] or sisa
            for i in lay:
                S2 = S | {i}
                c = f[S][0] + M.dur[i] * info["L"]
                if S2 not in f or c < f[S2][0] - 1e-12:
                    f[S2] = (c, i)
                baru[S2] = None
        lapis = list(baru)
    S, urut = frozenset(ids), []
    while S:
        i = f[S][1]
        urut.append(i)
        S = S - {i}
    return urut[::-1]


def susun(M, algo, iterasi=400, detik=30.0):
    if not M.putus:
        return []
    if algo == "krus":
        return dekode(M, sorted(M.putus, key=lambda i: -M.krus.get(i, 0.0)))
    g = urut_greedy(M)
    if algo == "greedy":
        return g
    if algo == "sa":
        return urut_sa(M, g, iterasi, detik)
    if algo == "optimal":
        return urut_optimal(M)
    raise ValueError(f"Algoritma tidak dikenal: {algo}")


# ---------------------------------------------------------------- jadwal & ringkasan
def jadwal(M, urut, kru=1):
    """Jadwalkan `urut` pada `kru` tim. Mengembalikan (DataFrame baris, titik kurva [(jam, L)])."""
    kru = max(int(kru), 1)
    bebas = [0.0] * kru
    rep = frozenset()
    siap = {i: 0.0 for i in M.hitung(rep)["layak"]}
    baris = []
    for k, i in enumerate(urut, 1):
        info = M.hitung(rep)
        darurat = bool(M.perlu_depot and i not in info["layak"])
        mob = M.mobilisasi(i, rep) / 3600
        c = min(range(kru), key=lambda x: bebas[x])
        mulai = max(bebas[c], 0.0 if darurat else siap.get(i, 0.0))
        selesai = mulai + M.dur[i] + mob
        bebas[c] = selesai
        rep2 = rep | {i}
        info2 = M.hitung(rep2)
        for e in info2["layak"] - info["layak"]:
            siap.setdefault(e, selesai)
        baris.append({
            "urutan": k, "ruas": i, "tim": c + 1,
            "mulai_jam": mulai, "selesai_jam": selesai,
            "durasi_jam": M.dur[i], "mobilisasi_jam": mob, "akses_darurat": darurat,
            "pulih_sebelum_pct": 100 * (1 - info["L"]), "pulih_sesudah_pct": 100 * (1 - info2["L"]),
            "rs_menit": info2["mentah"].get("rs", np.nan) / 60,
            "posko_menit": info2["mentah"].get("posko", np.nan) / 60,
        })
        rep = rep2
    df = pd.DataFrame(baris)
    titik = [(0.0, M.hitung(frozenset())["L"])]
    sel = set()
    for t, i in sorted((r["selesai_jam"], r["ruas"]) for r in baris):
        sel.add(i)
        titik.append((t, M.hitung(frozenset(sel))["L"]))
    return df, titik


def ringkas(titik):
    luas = sum(L0 * (t1 - t0) for (t0, L0), (t1, _) in zip(titik, titik[1:]))

    def kapan(p):
        for t, L in titik:
            if 1 - L >= p - 1e-9:
                return t
        return titik[-1][0]
    return {"selesai": titik[-1][0], "t50": kapan(.5), "t90": kapan(.9), "luas": luas}


def kurva_grid(titik, grid):
    """Persentase pemulihan (0-100) pada tiap jam di `grid` (fungsi tangga)."""
    ts = np.array([t for t, _ in titik])
    ls = np.array([L for _, L in titik])
    idx = np.clip(np.searchsorted(ts, grid, side="right") - 1, 0, None)
    return 100 * (1 - ls[idx])


# ---------------------------------------------------------------- perbandingan waktu tempuh
def rute(M, o, t, rep=None, normal=False):
    """Waktu (detik) dan daftar ruas jalur tercepat. normal=True: tanpa gangguan sama sekali."""
    blok = frozenset() if normal else (M.putus - (rep or frozenset()))
    f = M._fn(blok, pakai_lambat=not normal)
    try:
        w, jalur = nx.single_source_dijkstra(M.G, o, t, weight=f)
    except nx.NetworkXNoPath:
        return None, []
    idx = []
    for u, v in zip(jalur, jalur[1:]):
        i = M.G[u][v]["i"]
        if not idx or idx[-1] != i:
            idx.append(i)
    return w, idx


def bandingkan_od(M, rep):
    """Tabel waktu tempuh tiap perjalanan: normal, terdampak, dan setelah perbaikan `rep`; plus jalurnya."""
    def mnt(w):
        return np.nan if w is None else w / 60
    rows, jalur = [], {}
    for n, (nama, o, t, b) in enumerate(M.od):
        wn, jn = rute(M, o, t, normal=True)
        wd, jd = rute(M, o, t, rep=frozenset())
        wp, jp = rute(M, o, t, rep=frozenset(rep))
        rows.append({"perjalanan": nama, "normal_menit": mnt(wn), "terdampak_menit": mnt(wd), "pulih_menit": mnt(wp)})
        jalur[n] = {"normal": jn, "terdampak": jd, "pulih": jp}
    return pd.DataFrame(rows), jalur
