"""Menjalankan simulasi banjir sebagai satu paket: konteks, cache hasil, tugas latar belakang yang bisa dibatalkan,
banding beberapa skenario hujan, ambang hujan per ruas, dan kalibrasi dengan jejak banjir.

Tanpa ketergantungan Streamlit. Semua parameter skenario berupa dict sederhana (bisa disimpan sebagai JSON).
"""
import hashlib
import json
import threading
import time
from collections import OrderedDict

import numpy as np
import pandas as pd

import simulasi as sim

SISI = {"Utara": "U", "Selatan": "S", "Timur": "T", "Barat": "B"}
KONDISI_DRAINASE = {"baik": 1.0, "sedang": 0.6, "tersumbat": 0.25}      # pengali kapasitas drainase
LAJU_PASANG_JAM = 12.42                                                   # periode pasang semi-diurnal


class Konteks:
    """Data tetap untuk satu rangkaian simulasi: DEM, grid CN, dan titik sampel di sepanjang ruas."""

    def __init__(self, z, dx, asal, geoms_utm, cn_peta=None, sig=""):
        self.z, self.dx, self.asal = np.asarray(z, dtype=float), float(dx), (float(asal[0]), float(asal[1]))
        self.geoms = list(geoms_utm)
        self.cn_peta = cn_peta
        self.sampel = sim.titik_sampel(self.geoms, self.z.shape, self.dx, self.asal)
        self.sig = sig

    def sel_dari_xy(self, x, y):
        ny, nx = self.z.shape
        return (int(np.clip((self.asal[1] - y) // self.dx, 0, ny - 1)), int(np.clip((x - self.asal[0]) // self.dx, 0, nx - 1)))

    def sel_radius(self, x, y, radius_m):
        """Indeks sel (iy, ix) dalam radius (m); minimal sel di titik itu."""
        ny, nx = self.z.shape
        a, b = self.sel_dari_xy(x, y)
        k = int(np.ceil(max(radius_m, 0) / self.dx)) + 1
        y0, y1, x0, x1 = max(a - k, 0), min(a + k + 1, ny), max(b - k, 0), min(b + k + 1, nx)
        yy, xx = np.mgrid[y0:y1, x0:x1]
        cx = self.asal[0] + (xx + .5) * self.dx
        cy = self.asal[1] - (yy + .5) * self.dx
        m = np.hypot(cx - x, cy - y) <= max(radius_m, self.dx * .75)
        iy, ix = yy[m], xx[m]
        if not len(iy):
            iy, ix = np.array([a]), np.array([b])
        return iy, ix


# ---------------------------------------------------------------- parameter
def param_awal():
    return {"hujan": 150.0, "durasi": 3.0, "pola": "seragam", "hujan_tabel": None, "cn": 85.0, "amc": "normal",
            "drainase": 8.0, "kondisi_drainase": "baik", "manning": 0.05, "surut_jam": 6.0,
            "sungai": [], "sungai_hulu": [], "sungai_q": 20.0, "sungai_tp": 2.0, "sungai_lama": 8.0,
            "pasang": None, "intervensi": [], "cfl": None}


def kunci(ctx, p):
    """Kunci cache: konteks + semua parameter."""
    s = json.dumps(p, sort_keys=True, default=lambda o: np.asarray(o).tolist())
    return hashlib.md5((ctx.sig + "|" + s).encode()).hexdigest()[:14]


def _hidrograf(qp, tp, lama):
    tp = max(float(tp), 0.05)
    return np.array([0.0, tp, max(float(lama), tp + 0.1)]), np.array([0.0, float(qp), 0.0])


def _sumber(ctx, p):
    out = []
    for s in p.get("sungai") or []:
        iy, ix = ctx.sel_radius(s["x"], s["y"], s.get("radius_m", 60.0))
        t, q = _hidrograf(s["q"], s["tp"], s["lama"])
        out.append({"iy": iy, "ix": ix, "t_jam": t, "q": q})
    for x1, y1, x2, y2 in p.get("sungai_hulu") or []:      # garis sungai: masuk dari ujung yang lebih tinggi
        a, b = ctx.sel_dari_xy(x1, y1), ctx.sel_dari_xy(x2, y2)
        x, y = (x1, y1) if ctx.z[a] >= ctx.z[b] else (x2, y2)
        iy, ix = ctx.sel_radius(x, y, 60.0)
        t, q = _hidrograf(p.get("sungai_q", 20.0), p.get("sungai_tp", 2.0), p.get("sungai_lama", 8.0))
        out.append({"iy": iy, "ix": ix, "t_jam": t, "q": q})
    return out or None


def _pasang(z, p, total_jam):
    ps = p.get("pasang")
    if not ps:
        return None
    s = ps["sisi"]
    tepi = {"U": z[0, :], "S": z[-1, :], "T": z[:, -1], "B": z[:, 0]}[s]
    dasar = float(np.nanmin(tepi))
    t = np.arange(0.0, total_jam + 0.26, 0.25)
    amp = float(ps["rentang"]) / 2
    muka = dasar + float(ps["puncak"]) - amp + amp * np.cos(2 * np.pi * (t - float(ps["jam_puncak"])) / LAJU_PASANG_JAM)
    return {"sisi": s, "t_jam": t, "muka": muka}


def jalankan(ctx, p, progres=None, batal=None):
    """Satu simulasi lengkap -> dict hasil (kedalaman maksimum per sel dan per ruas, deret waktu per ruas)."""
    z = ctx.z
    iv = p.get("intervensi") or []
    sel = ctx.dx * ctx.dx
    faktor = float(KONDISI_DRAINASE.get(p.get("kondisi_drainase", "baik"), 1.0))
    pompa = None
    if iv:
        z = z.copy()
        faktor = np.full(z.shape, faktor)
        for v in iv:
            if v["jenis"] == "tinggikan" and v.get("ruas") is not None:
                i = int(v["ruas"])
                a, b = ctx.sampel["awal"][i], (ctx.sampel["awal"][i + 1] if i + 1 < len(ctx.sampel["awal"])
                                               else len(ctx.sampel["iy"]))
                import shapely
                g = ctx.geoms[i]
                n = max(int(g.length / (ctx.dx / 3)), 3)
                pts = shapely.line_interpolate_point(g, np.linspace(0, g.length, n + 1))
                ix = np.clip(((shapely.get_x(pts) - ctx.asal[0]) / ctx.dx).astype(int), 0, z.shape[1] - 1)
                iy = np.clip(((ctx.asal[1] - shapely.get_y(pts)) / ctx.dx).astype(int), 0, z.shape[0] - 1)
                z[iy, ix] += float(v["nilai"]) / 100.0
            elif v["jenis"] == "drainase":
                iy, ix = ctx.sel_radius(v["x"], v["y"], v.get("radius_m", 300.0))
                faktor[iy, ix] *= float(v["nilai"])
            elif v["jenis"] == "pompa":
                if pompa is None:
                    pompa = np.zeros(z.shape)
                iy, ix = ctx.sel_radius(v["x"], v["y"], v.get("radius_m", 60.0))
                pompa[iy, ix] += float(v["nilai"]) / (len(iy) * sel)

    cn = np.full(z.shape, float(p["cn"])) if ctx.cn_peta is not None else float(p["cn"])
    if ctx.cn_peta is not None:
        cn = np.where(np.isfinite(ctx.cn_peta), ctx.cn_peta, float(p["cn"]))
    cn = sim.cn_amc(cn, p.get("amc", "normal"))
    tabel = p.get("hujan_tabel")
    th, _ = sim.hietograf(p["hujan"], p["durasi"], p.get("pola", "seragam"), tabel)
    total_jam = th[-1] / 3600.0 + float(p.get("surut_jam", 6.0))
    hs = sim.simulasi(z, ctx.dx, p["hujan"], p["durasi"], cn, p["drainase"], p["manning"],
                      tambahan_jam=p.get("surut_jam", 6.0), progres=progres, pola=p.get("pola", "seragam"),
                      tabel_mm=tabel, faktor_drainase=faktor, sumber=_sumber(ctx, p), pasang=_pasang(z, p, total_jam),
                      pompa=pompa, sampel=ctx.sampel, batal=batal, cfl=p.get("cfl"))
    dmaks, dp90 = sim.kedalaman_dari_sampel(ctx.sampel, hs["h_maks"])
    return {"hs": hs, "dmaks": dmaks, "dp90": dp90, "seri": hs["seri"], "waktu_seri": hs["waktu_seri"], "p": p,
            "dx": ctx.dx, "asal": ctx.asal, "tgl": time.strftime("%H:%M:%S")}


def perkiraan_detik(ctx, p):
    """Perkiraan lama simulasi (detik): ukur waktu beberapa langkah lalu kalikan jumlah langkah yang diperkirakan."""
    z = ctx.z
    t0 = time.time()
    sim.simulasi(z, ctx.dx, 100.0, 1.0, 85.0, 0.0, 0.05, tambahan_jam=0.0, n_bingkai=1, maks_langkah=12)
    per_langkah = max((time.time() - t0) / 12.0, 1e-5)
    th, _ = sim.hietograf(p["hujan"], p["durasi"], p.get("pola", "seragam"), p.get("hujan_tabel"))
    total_t = th[-1] + float(p.get("surut_jam", 6.0)) * 3600.0
    dt = float(np.clip((p.get("cfl") or sim.CFL) * ctx.dx / np.sqrt(sim.G * 0.4), 0.5, 30.0))
    return per_langkah * total_t / dt * 1.25


# ---------------------------------------------------------------- cache dan tugas latar belakang
class Cache:
    """Hasil simulasi per kombinasi parameter (terbaru dipertahankan)."""

    def __init__(self, maks=10):
        self.d, self.maks, self.kunci_lock = OrderedDict(), maks, threading.Lock()

    def __contains__(self, k):
        return k in self.d

    def get(self, k):
        with self.kunci_lock:
            if k in self.d:
                self.d.move_to_end(k)
            return self.d.get(k)

    def put(self, k, v):
        with self.kunci_lock:
            self.d[k] = v
            self.d.move_to_end(k)
            while len(self.d) > self.maks:
                self.d.popitem(last=False)


class Job:
    """Beberapa simulasi berurutan di thread terpisah. Status: berjalan / selesai / batal / galat."""

    def __init__(self, jenis, tugas, cache, meta=None):
        self.jenis, self.tugas, self.cache, self.meta = jenis, list(tugas), cache, meta or {}
        self.status, self.galat, self.frac, self.diproses = "berjalan", None, 0.0, False
        self.mulai_t, self.ev = time.time(), threading.Event()
        self.ke, self.n = 0, max(len(self.tugas), 1)
        threading.Thread(target=self._jalan, daemon=True).start()

    def batal(self):
        self.ev.set()

    def sisa_detik(self):
        if self.frac < 0.03:
            return None
        return (time.time() - self.mulai_t) * (1 - self.frac) / self.frac

    def _jalan(self):
        try:
            for k, (kc, fn, ket) in enumerate(self.tugas):
                self.ke = k
                if self.ev.is_set():
                    self.status = "batal"
                    return
                if kc in self.cache:
                    self.frac = (k + 1) / self.n
                    continue
                res = fn(lambda f, k=k: setattr(self, "frac", (k + f) / self.n), self.ev.is_set)
                self.cache.put(kc, res)
                self.frac = (k + 1) / self.n
            self.status = "selesai"
        except sim.Dibatalkan:
            self.status = "batal"
        except Exception as e:  # noqa: BLE001  galat apa pun dilaporkan ke UI, bukan mematikan thread diam-diam
            self.galat, self.status = f"{type(e).__name__}: {e}", "galat"


def tugas_dari_param(ctx, daftar_param, ketlist=None):
    """Daftar parameter -> daftar (kunci, fungsi, keterangan) untuk Job."""
    out = []
    for n, p in enumerate(daftar_param):
        out.append((kunci(ctx, p), (lambda pr, b=ctx: (lambda progres, batal: jalankan(b, pr, progres, batal)))(p),
                    (ketlist or [""] * len(daftar_param))[n]))
    return out


# ---------------------------------------------------------------- analisis hasil
def hitung_kelas(res, amb):
    return sim.kelas_ruas(res["dmaks"], amb)


def ringkas(res, amb, panjang_m=None):
    kls = hitung_kelas(res, amb)
    d = {k: int((kls == k).sum()) for k in sim.KELAS_RUAS}
    d["tinggi_maks_m"] = float(res["hs"]["h_maks"].max())
    if panjang_m is not None:
        d["km_tergenang_atau_rusak"] = float(np.asarray(panjang_m)[(kls == "tergenang") | (kls == "rusak")].sum() / 1000)
    return d


def waktu_per_ruas(res, amb):
    """Jam mulai terputus, jam terbuka kembali, lama terputus, lama genangan (jam) per ruas dari deret waktu."""
    n = res["dmaks"].shape[0]
    if res.get("seri") is None:
        nan = np.full(n, np.nan)
        return {"putus_mulai": nan, "putus_selesai": nan, "putus_lama": nan, "genang_lama": nan, "genang_mulai": nan,
                "genang_selesai": nan, "belum_surut": np.zeros(n, bool)}
    jp = sim.jendela_waktu(res["seri"], res["waktu_seri"], amb[1])
    jg = sim.jendela_waktu(res["seri"], res["waktu_seri"], amb[0])
    return {"putus_mulai": jp["mulai"], "putus_selesai": jp["selesai"], "putus_lama": jp["durasi"],
            "genang_lama": jg["durasi"], "genang_mulai": jg["mulai"], "genang_selesai": jg["selesai"],
            "belum_surut": jp["belum"]}


def ambang_hujan(daftar, amb):
    """daftar = [(mm, hasil)]. Hujan minimum (mm) yang membuat tiap ruas terganggu / tergenang / rusak (interpolasi linear)."""
    daftar = sorted(daftar, key=lambda x: x[0])
    mm = np.array([a for a, _ in daftar], dtype=float)
    D = np.stack([r["dmaks"] for _, r in daftar])
    kolom = {}
    for nama, thr in zip(("terganggu", "tergenang", "rusak"), amb):
        ok = D >= thr
        ada = ok.any(axis=0)
        j = np.argmax(ok, axis=0)
        n = D.shape[1]
        hasil = np.full(n, np.nan)
        for i in np.where(ada)[0]:
            k = j[i]
            if k == 0:
                hasil[i] = mm[0] * thr / max(D[0, i], 1e-9)
            else:
                d0, d1 = D[k - 1, i], D[k, i]
                hasil[i] = mm[k - 1] + (mm[k] - mm[k - 1]) * (thr - d0) / max(d1 - d0, 1e-9)
        kolom[f"hujan_{nama}_mm"] = hasil
    return pd.DataFrame(kolom)


# ---------------------------------------------------------------- kalibrasi
def bandingkan_jejak(res, ctx, x, y, tinggi_obs):
    """Tinggi simulasi di titik jejak banjir vs pengamatan. Return (DataFrame, metrik)."""
    s = sim.kedalaman_xy(res["hs"]["h_maks"], ctx.dx, ctx.asal, x, y)
    o = np.asarray(tinggi_obs, dtype=float)
    sel = s - o
    m = {"n": int(len(o)), "rmse_m": float(np.sqrt(np.mean(sel ** 2))), "mae_m": float(np.mean(np.abs(sel))),
         "bias_m": float(np.mean(sel)), "pct_dalam_15cm": float(100 * np.mean(np.abs(sel) <= 0.15)),
         "korelasi": float(np.corrcoef(s, o)[0, 1]) if len(o) > 2 and np.std(s) > 0 and np.std(o) > 0 else np.nan}
    return pd.DataFrame({"obs_m": o, "sim_m": s, "selisih_m": sel}), m
