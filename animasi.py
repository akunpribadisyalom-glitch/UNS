"""Animasi simulasi perbaikan: truk berangkat dari quarry, ruas putus berubah merah -> hijau sesuai jadwal.

Bagian Python (siapkan) menghitung jadwal, jalur truk dari quarry lewat jalan yang sudah terbuka, dan peta
waktu animasi. Bagian JavaScript (JS) berjalan di peta Folium: tombol putar/jeda, kecepatan, dan penggeser waktu.
Tanpa ketergantungan Streamlit.
"""
import json
import math

import networkx as nx
from branca.element import MacroElement, Template

import gaya

DESIMAL = 5
DETIK_TOTAL = 45.0          # lama animasi kira-kira pada kecepatan 1x
MIN_JALAN = 1.8             # detik animasi minimal untuk fase truk berangkat
MIN_KERJA = 0.6
MIN_JEDA = 0.3


def _n(c):
    return (round(float(c[0]), DESIMAL), round(float(c[1]), DESIMAL))


def _bagian(geom, u, v):
    """Koordinat (lon, lat) bagian garis yang menghubungkan simpul u dan v, searah u -> v."""
    bagian = list(geom.geoms) if geom.geom_type == "MultiLineString" else [geom]
    for b in bagian:
        if b.geom_type != "LineString" or len(b.coords) < 2:
            continue
        a, z = _n(b.coords[0]), _n(b.coords[-1])
        if (a, z) == (u, v):
            return [(float(x), float(y)) for x, y in b.coords]
        if (a, z) == (v, u):
            return [(float(x), float(y)) for x, y in b.coords][::-1]
    return [u, v]


def _koor_jalur(G, geoms, path):
    """Daftar simpul -> daftar [lat, lon] mengikuti bentuk garis ruas (simpul bantu dilewati)."""
    nyata = [n for n in path if isinstance(n[0], float)]
    pts = []
    for u, v in zip(nyata, nyata[1:]):
        d = G.get_edge_data(u, v)
        seg = _bagian(geoms.iloc[d["i"]], u, v) if d else [u, v]
        pts += seg if not pts else seg[1:]
    if not pts and nyata:
        pts = [nyata[0]]
    return [[round(y, 6), round(x, 6)] for x, y in pts]


def _koor_ruas(geom):
    bagian = list(geom.geoms) if geom.geom_type == "MultiLineString" else [geom]
    pts = []
    for b in bagian:
        pts += [[round(float(y), 6), round(float(x), 6)] for x, y in b.coords]
    return pts


def _jalur_truk(M, sumber, i, rep):
    """Jalur terpendek dari sumber (quarry) ke ujung ruas i lewat ruas yang sudah terbuka. None bila terputus."""
    f = M._fn(M.putus - rep)
    dist, paths = nx.multi_source_dijkstra(M.G, sumber, weight=f)
    kand = [(dist[n], n) for n in M.ujung[i] if n in dist]
    if not kand:
        return None
    _, n = min(kand, key=lambda x: x[0])
    return paths[n]


def peta_waktu(jobs, tmax):
    """Pemetaan waktu nyata (jam) -> waktu animasi (detik). Fase truk berangkat diberi durasi minimal supaya terlihat."""
    t_ = {0.0, float(tmax)}
    for j in jobs:
        t_ |= {j["t0"], j["t1"], j["t2"]}
    b = sorted(t_)
    k = DETIK_TOTAL / (tmax or 1.0)
    out, tau = [[b[0], 0.0]], 0.0
    for a, z in zip(b, b[1:]):
        tengah = (a + z) / 2
        jalan = any(j["t0"] <= tengah < j["t1"] for j in jobs)
        kerja = any(j["t1"] <= tengah < j["t2"] for j in jobs)
        minimum = MIN_JALAN if jalan else MIN_KERJA if kerja else MIN_JEDA
        tau += max(k * (z - a), minimum)
        out.append([z, tau])
    return out


def siapkan(M, jd, nama, geoms, tit, titik_sumber):
    """Susun data animasi.

    M: Model; jd: DataFrame jadwal (kolom ruas, urutan, tim, mulai_jam, mobilisasi_jam, selesai_jam, akses_darurat);
    nama: Series label ruas; geoms: GeoSeries ruas (EPSG:4326); tit: titik kurva [(jam, L)];
    titik_sumber: DataFrame quarry (kolom nama, lat, lon, node) atau kosong -> tanpa truk.
    """
    sumber = set(titik_sumber["node"]) if titik_sumber is not None and len(titik_sumber) else set()
    jobs, selesai = [], []
    for r in jd.sort_values("mulai_jam").itertuples():
        i = int(r.ruas)
        t0, t2 = float(r.mulai_jam), float(r.selesai_jam)
        t1 = min(t0 + float(r.mobilisasi_jam), t2) if sumber else t0
        rep = frozenset(int(x) for x, ts in selesai if ts <= t0 + 1e-9)
        path = []
        if sumber and t1 > t0:
            try:
                p = _jalur_truk(M, sumber, i, rep)
            except Exception:
                p = None
            path = _koor_jalur(M.G, geoms, p) if p else []
        ruas_geo = _koor_ruas(geoms.iloc[i])
        jobs.append({"i": i, "no": int(r.urutan), "tim": int(r.tim), "nama": str(nama.iloc[i]),
                     "t0": t0, "t1": t1 if path else t0, "t2": t2, "path": path, "geom": ruas_geo,
                     "darurat": bool(r.akses_darurat)})
        selesai.append((i, t2))
    tmax = max([j["t2"] for j in jobs] + [1e-6])
    kurva = [[round(float(t), 4), round(100 * (1 - float(L)), 2)] for t, L in tit]
    quarry = []
    if sumber:
        for q in titik_sumber.itertuples():
            quarry.append([float(q.lat), float(q.lon), str(q.nama)])
    return {"jobs": jobs, "warp": peta_waktu(jobs, tmax), "kurva": kurva, "quarry": quarry, "tmax": tmax,
            "warna": {"ink": gaya.INK, "hijau": gaya.HIJAU, "merah": gaya.KORAL, "casing": gaya.CASING,
                      "kerja": gaya.KERJA}}


# ---------------------------------------------------------------- JavaScript di peta
JS = r"""
var K = D.jobs, W = D.warp, C = D.kurva, WR = D.warna;
var T = W[W.length - 1][1];
var N = K.length;

function esc(s) { return String(s).replace(/[&<>"]/g, function (c) {
  return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }); }
function jamTeks(j) {
  if (j >= 48) { var h = Math.floor(j / 24), s = Math.round(j - h * 24); return h + ' hari ' + s + ' jam'; }
  return j.toFixed(1).replace('.', ',') + ' jam';
}
function tOf(tau) {
  if (tau <= 0) return W[0][0];
  if (tau >= T) return W[W.length - 1][0];
  var lo = 0, hi = W.length - 1;
  while (hi - lo > 1) { var m = (lo + hi) >> 1; if (W[m][1] <= tau) lo = m; else hi = m; }
  var a = W[lo], b = W[hi];
  return a[0] + (b[0] - a[0]) * (tau - a[1]) / ((b[1] - a[1]) || 1);
}
function pctAt(t) {
  var p = 0;
  for (var i = 0; i < C.length; i++) { if (C[i][0] <= t + 1e-9) p = C[i][1]; else break; }
  return p;
}
function kum(pts) {
  var c = [0];
  for (var i = 1; i < pts.length; i++) {
    var dy = pts[i][0] - pts[i - 1][0];
    var dx = (pts[i][1] - pts[i - 1][1]) * Math.cos(pts[i][0] * Math.PI / 180);
    c.push(c[i - 1] + Math.sqrt(dx * dx + dy * dy));
  }
  return c;
}
function posisi(pts, cum, f) {
  if (!pts.length) return null;
  if (pts.length === 1) return pts[0];
  var tot = cum[cum.length - 1] * Math.min(Math.max(f, 0), 1);
  for (var i = 1; i < pts.length; i++) {
    if (cum[i] >= tot) {
      var s = (cum[i] - cum[i - 1]) || 1, u = (tot - cum[i - 1]) / s;
      return [pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * u, pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * u];
    }
  }
  return pts[pts.length - 1];
}
function potong(pts, cum, f) {
  if (pts.length < 2 || f <= 0) return [];
  if (f >= 1) return pts;
  var tot = cum[cum.length - 1] * f, out = [pts[0]];
  for (var i = 1; i < pts.length; i++) {
    if (cum[i] >= tot) { out.push(posisi(pts, cum, f)); return out; }
    out.push(pts[i]);
  }
  return out;
}

var css = document.createElement('style');
css.textContent =
  '@keyframes denyut{0%{transform:scale(.9);opacity:.95}70%{transform:scale(1.5);opacity:.15}100%{transform:scale(.9);opacity:.95}}' +
  '@keyframes ping{0%{transform:scale(.6);opacity:.9}100%{transform:scale(1.9);opacity:0}}' +
  '.ik{width:34px;height:34px;border-radius:50%;background:#fff;border:3px solid ' + WR.ink + ';display:flex;' +
  'align-items:center;justify-content:center;font-size:18px;box-shadow:2px 2px 0 ' + WR.ink + '}' +
  '.ik.kerja{animation:denyut 1s ease-in-out infinite;background:#fff6c9}' +
  '.ring{width:54px;height:54px;border-radius:50%;border:4px solid ' + WR.kerja + ';animation:ping 1.6s ease-out infinite}' +
  '.pnl{background:#fff;color:' + WR.ink + ';border:3px solid ' + WR.ink + ';border-radius:14px;padding:10px 12px;' +
  'width:262px;font:700 13px/1.3 Nunito,Arial,sans-serif;box-shadow:4px 4px 0 ' + WR.ink + '}' +
  '.pnl h4{margin:0 0 6px;font-size:15px;color:' + WR.ink + '}' +
  '.pnl .bar{height:12px;background:#e3e3e3;border:2px solid ' + WR.ink + ';border-radius:8px;overflow:hidden;margin:3px 0 6px}' +
  '.pnl .bar i{display:block;height:100%;background:' + WR.hijau + ';width:0}' +
  '.pnl button{border:2px solid ' + WR.ink + ';background:#fff;color:' + WR.ink + ';border-radius:10px;padding:3px 9px;' +
  'font:800 13px Nunito,Arial,sans-serif;cursor:pointer;margin-right:4px}' +
  '.pnl button:hover{background:#ffd166}' +
  '.pnl select{border:2px solid ' + WR.ink + ';background:#fff;color:' + WR.ink + ';border-radius:8px;font:800 13px Arial}' +
  '.pnl input[type=range]{width:100%;margin:6px 0 2px}' +
  '.pnl .akt{max-height:96px;overflow:auto;border-top:2px dashed ' + WR.ink + ';margin-top:6px;padding-top:5px;font-weight:700}' +
  '.pnl .akt div{margin:2px 0}' +
  '.pnl .lg{margin-top:6px;border-top:2px dashed ' + WR.ink + ';padding-top:5px;font-size:12px;color:' + WR.ink + '}' +
  '.pnl .lg span.sw{display:inline-block;width:20px;height:6px;border:1.5px solid #2b2b2b;vertical-align:middle;margin-right:5px;border-radius:4px}';
document.head.appendChild(css);

function ikon(html, cls, w) {
  return L.divIcon({html: '<div class="ik ' + (cls || '') + '">' + html + '</div>', className: '',
                    iconSize: [w || 34, w || 34], iconAnchor: [(w || 34) / 2, (w || 34) / 2]});
}
var IK_TRUK = ikon('🚚', '');
var IK_KERJA = ikon('👷', 'kerja');
var lap = L.layerGroup().addTo(peta);

D.quarry.forEach(function (q) {
  L.marker([q[0], q[1]], {icon: L.divIcon({html: '<div class="ring"></div>', className: '', iconSize: [54, 54],
    iconAnchor: [27, 27]}), interactive: false, zIndexOffset: -900}).addTo(lap);
});

K.forEach(function (j) {
  j.cp = kum(j.path); j.cg = kum(j.geom);
  j.mid = posisi(j.geom, j.cg, .5);
  j.casing = L.polyline([], {color: WR.casing, weight: 11, opacity: .9, lineCap: 'round'}).addTo(lap);
  j.prog = L.polyline([], {color: WR.hijau, weight: 6, opacity: 1, lineCap: 'round'}).addTo(lap);
  j.truk = L.marker(j.path.length ? j.path[0] : j.mid, {icon: IK_TRUK, interactive: false, zIndexOffset: 900});
  j.kerja = L.marker(j.mid, {icon: IK_KERJA, interactive: false, zIndexOffset: 800});
  j.sTruk = false; j.sKerja = false; j.sel = 0;
});

function pasang(mk, aktif, flag, j) {
  if (aktif && !j[flag]) { mk.addTo(lap); j[flag] = true; }
  if (!aktif && j[flag]) { lap.removeLayer(mk); j[flag] = false; }
}

var panel = L.control({position: 'topright'});
panel.onAdd = function () {
  var d = L.DomUtil.create('div', 'pnl');
  d.innerHTML =
    '<h4>Simulasi perbaikan</h4>' +
    '<div>Jam ke-<b id="aj">0</b> sejak bencana</div>' +
    '<div><b id="an">0</b> dari ' + N + ' ruas sudah diperbaiki</div>' +
    '<div class="bar"><i id="ab"></i></div>' +
    '<div>Pemulihan jaringan: <b id="ap">0</b>%</div>' +
    '<div style="margin-top:6px"><button id="ak">⏸ Jeda</button><button id="ar">↺ Ulang</button>' +
    '<select id="as"><option value="0.5">0,5×</option><option value="1" selected>1×</option>' +
    '<option value="2">2×</option><option value="4">4×</option></select></div>' +
    '<input id="al" type="range" min="0" max="1000" value="0">' +
    '<div class="akt" id="aa"></div>' +
    '<div class="lg"><span class="sw" style="background:#fff;border-style:dashed;border-color:' + WR.merah + '"></span>putus, menunggu ' +
    '<span class="sw" style="background:' + WR.hijau + '"></span>sudah diperbaiki<br>🚚 truk dari quarry &nbsp; 👷 tim bekerja</div>';
  L.DomEvent.disableClickPropagation(d);
  L.DomEvent.disableScrollPropagation(d);
  return d;
};
panel.addTo(peta);
var $ = function (id) { return document.getElementById(id); };

var tau = 0, main = true, kec = 1, last = null;
function gambar() {
  var t = tOf(tau), n = 0, rows = [];
  K.forEach(function (j) {
    var jalan = t >= j.t0 && t < j.t1, kerja = t >= j.t1 && t < j.t2, beres = t >= j.t2;
    var f = beres ? 1 : kerja ? (t - j.t1) / ((j.t2 - j.t1) || 1) : 0;
    if (beres) n++;
    if (f !== j.sel) {
      var pts = potong(j.geom, j.cg, f);
      j.casing.setLatLngs(pts); j.prog.setLatLngs(pts); j.sel = f;
    }
    var ada = j.path.length > 0;
    if (jalan && ada) j.truk.setLatLng(posisi(j.path, j.cp, (t - j.t0) / ((j.t1 - j.t0) || 1)));
    else if (ada && kerja) j.truk.setLatLng(j.path[j.path.length - 1]);
    pasang(j.truk, ada && (jalan || kerja), 'sTruk', j);
    pasang(j.kerja, kerja, 'sKerja', j);
    if (jalan) rows.push('🚚 Tim ' + j.tim + ' menuju <b>' + esc(j.nama) + '</b>');
    else if (kerja) rows.push('👷 Tim ' + j.tim + ' memperbaiki <b>' + esc(j.nama) + '</b> ' + Math.round(f * 100) + '%');
  });
  $('aj').textContent = jamTeks(t);
  $('an').textContent = n;
  var p = pctAt(t);
  $('ap').textContent = Math.round(p);
  $('ab').style.width = p + '%';
  $('al').value = Math.round(1000 * tau / (T || 1));
  $('aa').innerHTML = rows.length ? rows.join('') : (t >= W[W.length - 1][0] ? '<div>✅ Semua ruas sudah diperbaiki.</div>' : '<div>Menunggu tim berangkat...</div>');
}
function putar(ts) {
  if (last !== null && main) {
    tau += Math.min((ts - last) / 1000, .1) * kec;
    if (tau >= T) { tau = T; main = false; $('ak').textContent = '▶ Putar'; }
    gambar();
  }
  last = ts;
  requestAnimationFrame(putar);
}
$('ak').onclick = function () {
  if (!main && tau >= T) tau = 0;
  main = !main; this.textContent = main ? '⏸ Jeda' : '▶ Putar';
};
$('ar').onclick = function () { tau = 0; main = true; $('ak').textContent = '⏸ Jeda'; gambar(); };
$('as').onchange = function () { kec = parseFloat(this.value); };
$('al').oninput = function () { tau = T * this.value / 1000; gambar(); };
gambar();
requestAnimationFrame(putar);
"""

_TEMPLATE = Template(
    "{% macro script(this, kwargs) %}\n(function () {\nvar peta = {{ this._parent.get_name() }};\n"
    "var D = {{ this.data_json }};\n{% raw %}" + JS + "{% endraw %}\n})();\n{% endmacro %}")


class Animasi(MacroElement):
    """Tambahkan ke peta Folium: panel kendali + lapisan animasi."""
    _template = _TEMPLATE

    def __init__(self, data):
        super().__init__()
        self._name = "Animasi"
        self.data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
