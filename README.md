# 🛣️ Jalan Siaga (khusus banjir)

Aplikasi Streamlit: simulasi genangan dari hujan, klasifikasi ruas (terganggu / tergenang / rusak), fase selama banjir,
dan urutan serta animasi perbaikan dengan tim dan alat berat. Jalankan: `pip install -r requirements.txt` lalu `streamlit run app.py`.

## Alur
1. **Data**: seret semua file sekaligus. Perannya dibaca otomatis:
   jalan (garis, kolom `fl_max` opsional) · titik/CSV `nama,jenis,lat,lon,bobot` (RS, posko, alat berat, quarry, permukiman; bobot alat = jumlah unit) ·
   **DEM .tif** · **tutupan lahan** (poligon, kolom `cn` atau kelas lahan: permukiman, sawah, hutan, ...) · **garis sungai** ·
   **hujan per jam** (CSV kolom `jam,mm`, mis. prakiraan BMKG) · **jejak banjir** (CSV `lat,lon,tinggi` m atau cm) ·
   area gangguan · data perjalanan · **file proyek .json**. Menambah file tidak menghapus tanda manual; hanya mengganti jalan yang mereset.
2. **Simulasi banjir**: pilih preset kawasan, hujan, pola hujan, kondisi tanah → ▶️ Jalankan (di latar belakang, ada perkiraan waktu dan tombol batal).
   Hasil disimpan per kombinasi parameter, jadi skenario yang sama langsung muncul.
3. **Peta dan penanganan**: tandai ruas (klik atau gambar area), tambah titik, lihat urutan perbaikan, animasi, waktu tempuh, keadaan selama banjir.
4. **Hasil dan unduhan**: peringkat, banjir per ruas (jam mulai putus, lama genangan), ambang hujan per ruas, GeoTIFF, poligon genangan, PDF satu halaman.

## Tergenang atau rusak
| Kelas | Tinggi air (bawaan) | Perlakuan |
|---|---|---|
| terganggu | ≥ 15 cm | masih bisa lewat, lebih lambat |
| tergenang | ≥ 30 cm | putus sementara; **terbuka sendiri saat air surut** karena drainase, tidak masuk antrean perbaikan |
| rusak | ≥ 60 cm | banjir membawa sedimen dan menggerus jalan; **tetap putus** sesudah air surut, diperbaiki dengan alat berat, mulai setelah air surut |
Semua ambang bisa diubah di Pengaturan rinci.

## Model
Hujan (pola seragam / puncak awal-tengah-akhir / CSV) → limpasan SCS-CN kumulatif (CN seragam atau per sel dari tutupan lahan, disesuaikan
tanah kering/normal/basah) → aliran 2D inersia lokal (Bates dkk., 2010) dengan Manning. **Drainase** membuang genangan terus-menerus (mm/jam),
juga sesudah hujan reda. Opsional: debit sungai (hidrograf segitiga di hulu garis sungai atau titik), pasang di satu tepi peta, pompa,
saluran dibersihkan, ruas ditinggikan. Angka Courant bawaan 0,4 (mode Teliti 0,25): nilai 0,7 menumpuk air secara palsu pada DEM kasar.
Model skrining, bukan pengganti HEC-RAS 2D.

## Tim dan alat berat
Tim dasar + alat berat per tim + tabel tim tambahan (tiba jam ke-berapa, pangkalan). Kecepatan tim = jumlah alat ^ efek (bawaan 0,75).
Tab **Tim dan alat** menampilkan jam selesai untuk 1..N tim dan 1-3 alat per tim, serta jumlah minimum untuk mencapai target.

## Berkas
`app.py` halaman · `simulasi.py` hidrologi dan genangan · `skenario.py` konteks, cache, tugas latar belakang, ambang hujan, kalibrasi ·
`fase.py` isolasi dan evakuasi selama banjir · `restorasi.py` model, algoritma, tim · `pemulihan.py` penghubung · `ekspor.py` GeoTIFF, poligon, PDF ·
`ingest.py` pembaca file · `peta.py` · `animasi.py` · `gaya.py` · `scoring.py` · `nama_jalan.py` (manual, tombol).
