"""Gaya kartun-profesional: krem hangat, garis tebal navy, bayangan 'sticker', font bulat."""
import streamlit as st

# Satu warna tinta untuk semua teks dan garis tepi (tidak ada pasangan biru tua + hitam yang berdekatan).
INK = NAVY = "#1a1f36"
CASING = "#2b2b2b"     # garis tepi jalan di peta (netral, bukan biru)
KORAL, MATAHARI, MINT, LANGIT, UNGU, HIJAU, AMBER = (
    "#d62828", "#ffd166", "#2ec4b6", "#4cc9f0", "#6a1b9a", "#2e9d57", "#f28c00")
KERJA = "#1565c0"      # ruas yang sedang dikerjakan (animasi)

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Baloo+2:wght@600;700;800&family=Nunito:wght@400;600;700;800&display=swap');
:root{--navy:#1a1f36;--krem:#fff8ec;--sun:#ffd166;--koral:#ff6b6b;--mint:#2ec4b6;--langit:#4cc9f0;--ungu:#7b5cff}
html, body, .stApp, [data-testid="stAppViewContainer"]{font-family:'Nunito','Trebuchet MS',system-ui,sans-serif;color:var(--navy)}
.stApp{background:radial-gradient(circle at 1px 1px,#f0dcae 1.3px,transparent 1.5px) 0 0/22px 22px,var(--krem)}
h1,h2,h3,h4,h5{font-family:'Baloo 2','Trebuchet MS',sans-serif!important;color:var(--navy)!important;letter-spacing:.2px}
[data-testid="stHeader"]{background:transparent}
.block-container{padding-top:1.1rem;max-width:1500px}

.hero{background:linear-gradient(135deg,#ffd166 0%,#ff9f68 100%);border:3px solid var(--navy);border-radius:22px;
 padding:16px 24px;box-shadow:6px 6px 0 var(--navy);margin-bottom:1rem}
.hero h1{margin:0;font-size:2.1rem;line-height:1.15}
.hero p{margin:.3rem 0 0;font-weight:700}

[class*="st-key-kartu"]{background:#fff;border:3px solid var(--navy);border-radius:20px;padding:14px 18px;
 box-shadow:5px 5px 0 var(--navy);margin-bottom:.9rem}
[class*="st-key-kartu"] h3{margin-top:0}

.stButton>button,.stDownloadButton>button{border:2.5px solid var(--navy)!important;border-radius:14px!important;
 font-weight:800;box-shadow:3px 3px 0 var(--navy);background:#fff;color:var(--navy);
 transition:transform .08s,box-shadow .08s}
.stButton>button:hover,.stDownloadButton>button:hover{transform:translate(-1px,-1px);box-shadow:4px 4px 0 var(--navy);
 background:var(--sun);color:var(--navy);border-color:var(--navy)!important}
.stButton>button:active{transform:translate(3px,3px);box-shadow:0 0 0 var(--navy)}
.stButton>button[kind="primary"]{background:#c62828;color:#fff}
.stButton>button[kind="primary"]:hover{background:var(--sun);color:var(--navy)}
.stButton>button p,.stDownloadButton>button p{color:inherit}

[data-testid="stFileUploaderDropzone"]{border:3px dashed var(--navy);border-radius:20px;background:#fff3c9}
[data-testid="stMetric"]{background:#fff;border:2.5px solid var(--navy);border-radius:16px;padding:8px 14px;
 box-shadow:4px 4px 0 var(--navy)}
[data-testid="stExpander"]{border:2.5px solid var(--navy)!important;border-radius:16px!important;background:#fff;
 box-shadow:4px 4px 0 var(--navy);margin-bottom:.7rem}
[data-testid="stSegmentedControl"] button{border:2px solid var(--navy)!important;font-weight:800;
 background:#fff!important;color:var(--navy)!important}
[data-testid="stSegmentedControl"] button p{color:inherit!important}
[data-testid="stSegmentedControl"] button[aria-checked="true"],
[data-testid="stSegmentedControl"] button[kind="segmented_controlActive"]{background:var(--navy)!important;color:#fff!important}
[data-testid="stSegmentedControl"] button:hover{background:var(--sun)!important;color:var(--navy)!important}
iframe{border:3px solid var(--navy)!important;border-radius:20px;box-shadow:6px 6px 0 var(--navy)}
[data-testid="stDataFrame"],[data-testid="stDataEditor"]{border:2.5px solid var(--navy);border-radius:14px;overflow:hidden}

.chip{display:inline-block;border:2px solid var(--navy);border-radius:999px;padding:2px 12px;margin:3px 6px 3px 0;
 font-weight:800;background:#fff;font-size:.9rem}
.chip.ok{background:#d9fbe8}.chip.warn{background:#fff0c2}
.langkah{display:flex;gap:10px;align-items:center;background:#fff;border:2.5px solid var(--navy);border-radius:14px;
 padding:7px 12px;margin:6px 0;box-shadow:3px 3px 0 var(--navy)}
.langkah .no{min-width:34px;height:34px;border-radius:50%;background:var(--koral);color:#fff;border:2.5px solid var(--navy);
 display:flex;align-items:center;justify-content:center;font-family:'Baloo 2',sans-serif;font-weight:800}
.langkah .jdl{font-weight:800;line-height:1.15}.langkah .sub{font-size:.82rem;color:#3b4468}
.kosong{text-align:center;background:#fff;border:3px dashed var(--navy);border-radius:20px;padding:22px 14px;font-weight:700}
.kosong .em{font-size:2.6rem;display:block}
.ringkas{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:4px 0 10px}
.ringkas div{background:#fff;border:2.5px solid var(--navy);border-radius:14px;padding:6px 10px;box-shadow:3px 3px 0 var(--navy)}
.ringkas b{display:block;font-family:'Baloo 2',sans-serif;font-size:1.35rem;line-height:1.1}
.ringkas span{font-size:.78rem;font-weight:700;color:#3b4468}
[data-testid="stCaptionContainer"],[data-testid="stCaptionContainer"] *{color:#3b4468!important}
label,[data-testid="stWidgetLabel"] p,[data-testid="stMarkdownContainer"] p,[data-testid="stMarkdownContainer"] li{color:var(--navy)}
[data-testid="stFileUploaderDropzone"] *{color:var(--navy)}
[data-testid="stExpander"] summary,[data-testid="stExpander"] summary *{color:var(--navy)!important}
[data-testid="stAlert"] *{color:var(--navy)!important}
.legenda{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 2px}
.legenda .item{display:inline-flex;align-items:center;gap:7px;background:#fff;color:var(--navy);border:2px solid var(--navy);
 border-radius:999px;padding:2px 12px 2px 8px;font-weight:800;font-size:.86rem}
.legenda .sw{display:inline-block;width:26px;height:8px;border-radius:5px;border:1.5px solid #2b2b2b}
</style>
"""


def terapkan():
    st.markdown(CSS, unsafe_allow_html=True)


def chip(teks, kelas=""):
    return f"<span class='chip {kelas}'>{teks}</span>"


def legenda(items):
    """items = [(warna, teks, putus_putus)] -> legenda dengan teks gelap di latar putih."""
    h = ""
    for warna, teks, putus in items:
        isi = f"repeating-linear-gradient(90deg,{warna} 0 6px,#fff 6px 10px)" if putus else warna
        h += f"<span class='item'><span class='sw' style='background:{isi}'></span>{teks}</span>"
    return f"<div class='legenda'>{h}</div>"


def jam_teks(j):
    """Jam -> '2 hari 5 jam' / '7,5 jam'."""
    if j >= 48:
        h, s = divmod(round(j), 24)
        return f"{h} hari {s} jam" if s else f"{h} hari"
    return f"{j:.1f} jam".replace(".", ",")
