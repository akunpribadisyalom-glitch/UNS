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
