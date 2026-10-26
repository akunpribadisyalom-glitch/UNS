"""Perhitungan skor krusialitas ruas jalan (tanpa ketergantungan Streamlit)."""
import pandas as pd

KELAS = ["Sangat kritis", "Tinggi", "Sedang", "Rendah"]
WARNA = {"Sangat kritis": "#c62828", "Tinggi": "#ef6c00", "Sedang": "#e0a800", "Rendah": "#2e9d57"}
LEBAR = {"Sangat kritis": 6, "Tinggi": 4.5, "Sedang": 3, "Rendah": 2}
KATA_STATUS = {"arteri": ["arteri"], "kolektor": ["kolektor"], "lokal": ["lokal", "lingkungan"]}


def to_num(s):
    """Ubah ke angka; teks berkoma desimal ('0,35') ikut terbaca."""
    if s.dtype == object:
        s = s.astype(str).str.replace(",", ".", regex=False)
    return pd.to_numeric(s, errors="coerce")


def minmax(s):
    s = to_num(s)
    lo, hi = s.min(), s.max()
    if pd.isna(lo) or hi == lo:
        return pd.Series(0.0, index=s.index)
    return ((s - lo) / (hi - lo)).fillna(0.0)


def hazard(df, cols, how="union", weights=None, norm=False, fill_min=True):
    """Gabungkan kolom bahaya menjadi satu nilai 0-1 per ruas."""
    P = pd.concat([to_num(df[c]) for c in cols], axis=1)
    P.columns = cols
    if fill_min:                      # NULL -> nilai minimum kolom itu
        P = P.fillna(P.min())
    P = P.fillna(0.0)
    P = P.apply(lambda c: c / 100 if c.max() > 1 else c)   # persen -> 0-1
    if norm:
        P = P.apply(minmax)
    P = P.clip(0, 1)
    if how == "max":
        return P.max(axis=1)
    if how == "union":
        return 1 - (1 - P).prod(axis=1)
    w = pd.Series(weights or {}, dtype=float).reindex(cols).fillna(1.0)
    return (P * w).sum(axis=1) / (w.sum() or 1.0)


def status(s, nilai):
    """Teks (arteri/kolektor/lokal) -> 0-1 sesuai dict `nilai`; angka -> min-max."""
    n = to_num(s)
    if n.notna().all():
        return minmax(n)

    def f(x):
        x = str(x).lower()
        for kunci, kata in KATA_STATUS.items():
            if any(k in x for k in kata):
                return nilai[kunci]
        return nilai["lainnya"]
    return s.map(f).astype(float)


def rata_minmax(df, cols, invert=False):
    parts = [minmax(df[c]) for c in cols]
    if invert:
        parts = [1 - p for p in parts]
    return pd.concat(parts, axis=1).mean(axis=1)


def hitung(H, S, K, D, w, mode="mul"):
    """w = {'h','s','k','d'} bobot. mode 'mul' = Bahaya x Konsekuensi, 'add' = jumlah terbobot."""
    cs = w["s"] + w["k"] + w["d"]
    if mode == "mul":
        C = (w["s"] * S + w["k"] * K + w["d"] * D) / cs if cs else 1.0
        skor = H * C
    else:
        skor = (w["h"] * minmax(H) + w["s"] * S + w["k"] * K + w["d"] * D) / ((w["h"] + cs) or 1.0)
    urut = skor.rank(ascending=False, method="first")
    kelas = pd.cut(urut / len(skor), [0, .1, .3, .6, 1.0], labels=KELAS)
    return pd.DataFrame({"peringkat": urut.astype(int), "skor": skor, "kelas": kelas.astype(str)})
