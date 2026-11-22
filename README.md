# 🛣️ Jalan Siaga (khusus banjir)

Aplikasi Streamlit untuk urutan dan animasi perbaikan jalan pascabanjir. Bahaya hanya dari **probabilitas banjir `fl_max`**.

## Cara pakai
1. **Unggah** semua file sekaligus. Perannya dibaca otomatis:
   - garis terbanyak = jalan (GeoJSON, GeoPackage, Shapefile .zip) dengan kolom `fl_max`
   - titik atau CSV `nama,jenis,lat,lon,bobot` = RS, posko, alat berat, **quarry**, permukiman
   - poligon (atau nama file berisi banjir/genangan) = area gangguan
   - CSV `nama,asal_lat,asal_lon,tujuan_lat,tujuan_lon,waktu_menit,bobot` = data perjalanan
   - **GeoTIFF (.tif) = DEM** (raster ketinggian) untuk simulasi banjir dari curah hujan
   - CSV berisi nomor ruas + `fl_max` baru (mis. hasil simulasi hujan) = **menggantikan** `fl_max` di jalan
     (dicocokkan lewat kolom nomor ruas yang namanya sama)
2. **Tandai jalan putus** di peta (klik ruas atau gambar area). Urutan perbaikan muncul otomatis.
3. Buka tab **🎬 Animasi perbaikan**: truk berangkat dari quarry ke tiap ruas putus, ruas berubah merah → hijau.

## Simulasi banjir dari curah hujan
Unggah **DEM (.tif)** bersama file jalan, buka panel **🌧️ Simulasi banjir dari curah hujan**, isi curah hujan (mm), lama hujan (jam),
dan Curve Number, lalu **▶️ Jalankan simulasi**. Hasilnya:
- genangan di peta (biru muda → tua → ungu) dengan penggeser waktu (naik dan surut),
- **tinggi banjir (m) di tiap ruas jalan** (muncul saat kursor diarahkan ke ruas, dan di CSV unduhan),
- ruas dengan tinggi ≥ ambang otomatis bisa ditandai **terganggu/putus** (tombol ✅ Terapkan), lalu urutan perbaikan dihitung seperti biasa.

Persyaratan DEM: GeoTIFF dengan CRS, menutupi seluruh jalan. Sumber gratis: DEMNAS (8 m, tanahair.indonesia.go.id),
Copernicus GLO-30 atau SRTM (30 m). Tidak perlu menambah kolom apa pun di GeoJSON jalan.
Metode: limpasan SCS-CN, lalu aliran 2D inersia lokal (Bates dkk., 2010) dengan Manning. Model skrining, bukan pengganti HEC-RAS 2D.
Modul inti `simulasi.py` tidak bergantung pada Streamlit sehingga bisa diuji terpisah.

## Warna
Jalan: hijau = aman, kuning = rawan sedang, merah = rawan tinggi (batas bisa diubah). Ruas putus: merah putus-putus
bertepi gelap; terganggu: oranye putus-putus; sudah diperbaiki: hijau penuh.

## Nama jalan
Nomor ruas dicocokkan dengan jalan OpenStreetMap (Overpass API, butuh internet). Hasil disimpan di folder sementara.
Bila file jalan sudah punya kolom nama jalan, kolom itu dipakai dan OSM hanya mengisi yang kosong.

## Berkas
`app.py` halaman utama, `simulasi.py` simulasi banjir hujan, `ingest.py` pembaca file, `nama_jalan.py` pencari nama jalan, `animasi.py` animasi,
`pemulihan.py` penghubung model, `peta.py` peta, `gaya.py` tampilan, `restorasi.py` model dan algoritma,
`scoring.py` kelas banjir, `.streamlit/config.toml` tema.

Jalankan: `pip install -r requirements.txt` lalu `streamlit run app.py`.
