# 🛣️ Jalan Siaga

Aplikasi Streamlit untuk krusialitas jaringan jalan dan urutan perbaikan pascabencana.

## Cara pakai (3 langkah)
1. **Unggah** semua file sekaligus. Perannya dibaca otomatis:
   - garis terbanyak = jalan (GeoJSON, GeoPackage, Shapefile .zip)
   - titik atau CSV `nama,jenis,lat,lon,bobot` = RS, posko, alat berat, quarry, permukiman
     (jenis dibaca dari kolom `jenis/type/amenity/...` atau dari nama file)
   - poligon (atau nama file berisi banjir/longsor/genangan) = area gangguan
   - CSV `nama,asal_lat,asal_lon,tujuan_lat,tujuan_lon,waktu_menit,bobot` = data perjalanan
2. **Pilih bencana**: banjir, longsor, gempa, atau umum.
3. **Tandai jalan putus** langsung di peta (klik ruas, atau gambar area), urutan perbaikan terbaik muncul otomatis.

Peta membuka lokasi data yang diunggah (tidak terkunci di satu daerah). Titik RS/posko/alat berat/quarry/permukiman
juga bisa ditambah manual: pilih mode di atas peta, lalu klik lokasinya.

## Berkas
`app.py` halaman utama, `ingest.py` pembaca/penebak file, `pemulihan.py` penghubung model, `peta.py` peta,
`gaya.py` tampilan, `restorasi.py` model dan algoritma (greedy, simulated annealing, optimal DP), `scoring.py` skor krusialitas,
`.streamlit/config.toml` tema.

Jalankan: `pip install -r requirements.txt` lalu `streamlit run app.py`.
