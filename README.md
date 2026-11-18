# 🛣️ Jalan Siaga (khusus banjir)

Aplikasi Streamlit untuk urutan dan animasi perbaikan jalan pascabanjir. Bahaya hanya dari **probabilitas banjir `fl_max`**.

## Cara pakai
1. **Unggah** semua file sekaligus. Perannya dibaca otomatis:
   - garis terbanyak = jalan (GeoJSON, GeoPackage, Shapefile .zip) dengan kolom `fl_max`
   - titik atau CSV `nama,jenis,lat,lon,bobot` = RS, posko, alat berat, **quarry**, permukiman
   - poligon (atau nama file berisi banjir/genangan) = area gangguan
   - CSV `nama,asal_lat,asal_lon,tujuan_lat,tujuan_lon,waktu_menit,bobot` = data perjalanan
   - CSV berisi nomor ruas + `fl_max` baru (mis. hasil simulasi hujan) = **menggantikan** `fl_max` di jalan
     (dicocokkan lewat kolom nomor ruas yang namanya sama)
2. **Tandai jalan putus** di peta (klik ruas atau gambar area). Urutan perbaikan muncul otomatis.
3. Buka tab **🎬 Animasi perbaikan**: truk berangkat dari quarry ke tiap ruas putus, ruas berubah merah → hijau.

## Warna
Jalan: hijau = aman, kuning = rawan sedang, merah = rawan tinggi (batas bisa diubah). Ruas putus: merah putus-putus
bertepi gelap; terganggu: oranye putus-putus; sudah diperbaiki: hijau penuh.

## Nama jalan
Nomor ruas dicocokkan dengan jalan OpenStreetMap (Overpass API, butuh internet). Hasil disimpan di folder sementara.
Bila file jalan sudah punya kolom nama jalan, kolom itu dipakai dan OSM hanya mengisi yang kosong.

## Berkas
`app.py` halaman utama, `ingest.py` pembaca file, `nama_jalan.py` pencari nama jalan, `animasi.py` animasi,
`pemulihan.py` penghubung model, `peta.py` peta, `gaya.py` tampilan, `restorasi.py` model dan algoritma,
`scoring.py` kelas banjir, `.streamlit/config.toml` tema.

Jalankan: `pip install -r requirements.txt` lalu `streamlit run app.py`.
