# Krusialitas Jaringan Jalan

Aplikasi Streamlit untuk memeringkat ruas jalan berdasarkan bahaya bencana, status jalan, konektivitas, dan densitas permukiman, lalu menampilkannya di peta berwarna.

## Menjalankan
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy (Streamlit Community Cloud)
1. Push folder ini ke GitHub.
2. Di share.streamlit.io pilih repo, branch `main`, main file `app.py`.

## Data masukan
Dari QGIS: **Export → Save Features As** → GeoJSON, GeoPackage, atau Shapefile (di-zip). CRS apa pun boleh, aplikasi mengubahnya ke EPSG:4326 sendiri. Tiap baris = satu ruas jalan (garis) dengan kolom bahaya, status, konektivitas, dan densitas. Atribut juga bisa dari Excel/CSV terpisah, digabung lewat kolom kunci (mis. `id_ruas`).

## Simulasi pemulihan (tab "Simulasi pemulihan")
1. Pilih jenis bencana (banjir, longsor, gempa, umum): bobot prioritas berubah (banjir: akses RS; longsor: akses alat berat).
2. Tandai ruas putus/terganggu: klik ruas, gambar area di peta, unggah GeoJSON gangguan, atau skenario acak berbobot bahaya.
3. Isi titik RS/posko/depot/permukiman (unggah GeoJSON/CSV atau isi tabel) dan data perjalanan (otomatis atau unggah).
4. Jalankan algoritma (greedy, simulated annealing, optimal DP, urut krusialitas, acak) dan bandingkan kurva pemulihan.
5. Lihat waktu tempuh normal, terdampak, dan setelah perbaikan pada peta.

Format tabel perjalanan: `nama, asal_lat, asal_lon, tujuan_lat, tujuan_lon, waktu_menit, bobot`.
Format tabel titik: `nama, jenis, lat, lon, bobot` (jenis: rs, posko, depot, permukiman).
