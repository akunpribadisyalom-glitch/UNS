"""Fase sebelum dan selama banjir: jam ke berapa warga dan RS terisolasi, jalur evakuasi ke posko yang masih bisa
dilewati, dan penduduk terdampak genangan. Tanpa ketergantungan Streamlit.

Keadaan jaringan tiap waktu = himpunan ruas terblokir (tergenang >= ambang tergenang, atau rusak sesudah air mencapainya)
dan ruas terganggu (lebih lambat).
"""
import networkx as nx
import numpy as np
import pandas as pd

HAMBATAN = 0.3      # kecepatan ruas terganggu relatif terhadap normal


def _pilih(waktu, langkah):
    target = np.arange(0.0, float(waktu[-1]) + 1e-9, langkah)
    idx = np.unique(np.clip(np.searchsorted(waktu, target - 1e-9), 0, len(waktu) - 1))
    return idx


def kejadian_sim(seri, waktu, rusak, amb_terganggu, amb_tergenang, langkah_jam=1.0):
    """Deret waktu kedalaman per ruas -> keadaan jaringan tiap `langkah_jam` (nilai maks di tiap selang)."""
    seri, waktu = np.asarray(seri), np.asarray(waktu, dtype=float)
    idx = _pilih(waktu, langkah_jam)
    agg = np.maximum.reduceat(seri, idx, axis=0)
    blok = agg >= amb_tergenang
    for i in rusak:
        ada = np.where(agg[:, i] >= amb_tergenang)[0]
        blok[(ada[0] if len(ada) else 0):, i] = True
    return {"waktu": waktu[idx], "blok": blok, "lambat": (agg >= amb_terganggu) & ~blok}


def kejadian_tanda(n_ruas, putus, tergenang, lambat, lama_jam=12.0, jendela=None, horizon=None):
    """Tanda manual: rusak selalu putus; tergenang dan terganggu hanya selama `lama_jam` (atau jendela {ruas: (mulai, selesai)})."""
    horizon = horizon or max(24.0, 2 * lama_jam)
    w = np.arange(0.0, horizon + 1e-9, 1.0)
    blok = np.zeros((len(w), n_ruas), bool)
    lam = np.zeros_like(blok)
    jendela = jendela or {}
    for i in putus:
        blok[:, i] = True
    for i in tergenang:
        a, b = jendela.get(i, (0.0, lama_jam))
        blok[(w >= a) & (w < b), i] = True
    for i in lambat:
        lam[w < lama_jam, i] = True
    return {"waktu": w, "blok": blok, "lambat": lam & ~blok}


def _bobot(G, blok, lambat, lam=HAMBATAN):
    def f(u, v, d):
        i = d["i"]
        if blok[i]:
            return None
        return d["t"] / (lam if lambat[i] else 1.0)
    return f


def analisis(G, ti, kej, lam=HAMBATAN):
    """Isolasi warga dan RS sepanjang waktu. ti = hasil pemulihan.olah_titik. Return dict ringkas + per permukiman."""
    perm = ti["perm"]
    w, blok, lamb = kej["waktu"], kej["blok"], kej["lambat"]
    K = len(w)
    pop = np.array([float(b) for _, _, b in perm]) if perm else np.zeros(0)
    tot = float(pop.sum())
    iso_rs = np.zeros((K, len(perm)), bool)
    iso_pk = np.zeros((K, len(perm)), bool)
    baris = []
    for k in range(K):
        H = nx.subgraph_view(G, filter_edge=lambda u, v, b=blok[k]: not b[G[u][v]["i"]])
        comp = {}
        for cid, c in enumerate(nx.connected_components(H)):
            for n in c:
                comp[n] = cid
        c_rs = {comp[n] for n in ti["rs"] if n in comp}
        c_pk = {comp[n] for n in ti["posko"] if n in comp}
        c_pm = {comp.get(n, -1) for _, n, _ in perm}
        if ti["rs"]:
            iso_rs[k] = [comp.get(n, -1) not in c_rs for _, n, _ in perm]
        if ti["posko"]:
            iso_pk[k] = [comp.get(n, -1) not in c_pk for _, n, _ in perm]
        rs_iso = sum(comp.get(r, -1) not in c_pm for r in ti["rs"]) if perm else 0
        mnt = np.nan
        if ti["rs"] and perm:
            d = nx.multi_source_dijkstra_path_length(G, ti["rs"], weight=_bobot(G, blok[k], lamb[k], lam))
            ok = [(d[n] / 60, b) for _, n, b in perm if n in d]
            if ok:
                mnt = sum(a * b for a, b in ok) / sum(b for _, b in ok)
        baris.append({"jam": float(w[k]), "ruas_terblokir": int(blok[k].sum()), "ruas_terganggu": int(lamb[k].sum()),
                      "pop_terisolasi_rs": float(pop[iso_rs[k]].sum()), "pop_terisolasi_posko": float(pop[iso_pk[k]].sum()),
                      "rs_terisolasi": int(rs_iso), "menit_ke_rs": mnt})
    tab = pd.DataFrame(baris)
    if tot > 0:
        tab["pct_terisolasi_rs"] = 100 * tab["pop_terisolasi_rs"] / tot
        tab["pct_terisolasi_posko"] = 100 * tab["pop_terisolasi_posko"] / tot
    langkah = float(np.diff(w).mean()) if K > 1 else 1.0
    rows = []
    for j, (nama, n, b) in enumerate(perm):
        m = iso_rs[:, j]
        ada = m.any()
        pertama = int(np.argmax(m)) if ada else None
        akhir = K - 1 - int(np.argmax(m[::-1])) if ada else None
        rows.append({"permukiman": nama, "penduduk": float(b),
                     "isolasi_mulai_jam": float(w[pertama]) if ada else np.nan,
                     "tersambung_lagi_jam": float(w[min(akhir + 1, K - 1)]) if ada and akhir < K - 1 else np.nan,
                     "lama_isolasi_jam": float(m.sum() * langkah),
                     "belum_tersambung": bool(ada and akhir >= K - 1)})
    return {"tabel": tab, "perm": pd.DataFrame(rows), "iso_rs": iso_rs, "iso_posko": iso_pk, "total_pop": tot}


def evakuasi(G, ti, kej, k, lam=HAMBATAN):
    """Jalur terpendek tiap permukiman ke posko terdekat pada indeks waktu k. Return DataFrame (kolom ruas = daftar indeks)."""
    perm = ti["perm"]
    if not ti["posko"] or not perm:
        return pd.DataFrame(columns=["permukiman", "penduduk", "menit", "tersambung", "ruas"])
    dist, paths = nx.multi_source_dijkstra(G, ti["posko"], weight=_bobot(G, kej["blok"][k], kej["lambat"][k], lam))
    rows = []
    for nama, n, b in perm:
        if n in dist:
            p = paths[n][::-1]
            idx = []
            for u, v in zip(p, p[1:]):
                i = G[u][v]["i"]
                if not idx or idx[-1] != i:
                    idx.append(i)
            rows.append({"permukiman": nama, "penduduk": float(b), "menit": dist[n] / 60, "tersambung": True, "ruas": idx})
        else:
            rows.append({"permukiman": nama, "penduduk": float(b), "menit": np.nan, "tersambung": False, "ruas": []})
    return pd.DataFrame(rows)


def terdampak(depth_titik, bobot, ambang):
    """Penduduk di titik permukiman dengan kedalaman maksimum >= ambang (m)."""
    d = np.asarray(depth_titik, dtype=float)
    return float(np.asarray(bobot, dtype=float)[d >= ambang].sum()), int((d >= ambang).sum())
