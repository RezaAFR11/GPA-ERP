# Jadwal kerja dan klarifikasi absensi

## Perilaku

- Hanya role persis `SUPER_ADMIN` dan `HR` yang boleh mengelola template,
  penugasan, pembatalan jadwal, riwayat, dan persetujuan klarifikasi. Hak tersebut
  tidak diwariskan ke GA, MD, PM, atau PROJECT_CONTROL.
- Template menentukan jam masuk lokal, awal istirahat, toleransi terlambat dan
  menit pengingat. Durasi masuk-pulang 9 jam, istirahat 1 jam, kerja 8 jam.
  Jam pulang dihitung otomatis, termasuk pergantian tanggal.
- HR memilih karyawan atau grup, shift, lokasi dan hari kerja. Jadwal mingguan
  berulang tanpa tanggal akhir, sampai diubah atau dinonaktifkan. Tidak ada kolom
  Mulai/Sampai atau centang ganti. Maksimal 500 karyawan sekali pengiriman.
  Keanggotaan grup disalin saat penugasan; anggota baru perlu ditetapkan jadwalnya.
- Menyimpan langsung mengganti aturan mingguan dan tanggal mendatang yang belum
  memiliki absensi, termasuk menghapus jadwal pada hari yang tidak lagi dipilih.
  Hari ini boleh dijadwalkan walaupun jam masuk sudah lewat, selama belum absen.
  Jika shift sudah berakhir, jadwal dipakai pada hari kerja berikutnya.
  Keterlambatan tetap dihitung dari jam masuk, tanpa pengecualian otomatis.
- Sesi yang sudah memiliki absensi tetap memakai snapshot lama. Perubahan pada
  hari yang sudah digunakan berlaku mulai hari berikutnya. Jadwal yang beririsan
  dengan sesi lama dilewati, termasuk perubahan setelah shift lintas tengah malam.
- Template yang diedit tidak mengubah jadwal karyawan secara diam-diam; tetapkan
  ulang agar aturan baru digunakan. Tombol Nonaktifkan menghentikan pengulangan
  dan membersihkan tanggal mendatang yang belum digunakan, tanpa menghapus absensi.
- Aturan mingguan disimpan permanen. Tanggal kerja dibuat otomatis untuk jendela
  32 hari berikutnya saat portal dibuka atau saat clock-in, menggunakan waktu
  server dan zona penugasan. Jendela ini selalu diperpanjang sesuai kebutuhan,
  bukan batas berakhirnya jadwal. Employee row lock mencegah penugasan/clock-in
  bersamaan menghasilkan tanggal ganda.
- Jadwal per tanggal dari versi sebelumnya tetap berlaku sampai HR menggantinya
  dengan jadwal mingguan. Migrasi tidak menebak pola mingguan dari jadwal lama.
  Rotasi otomatis dan penugasan sementara belum ditambahkan ke form mingguan.
- Pop-up di tengah layar membedakan keberhasilan simpan template, tetapkan jadwal,
  dan nonaktifkan jadwal; kegagalan menampilkan penyebab. Daftar diperbarui sesudah
  respons berhasil, dan hasil penugasan menjelaskan jumlah karyawan/sesi terlindungi.
- Absen masuk dibuka 2 jam sebelum mulai hingga sebelum akhir shift. Karyawan
  tidak memilih shift. Tidak ada jadwal berarti clock-in ditolak dengan pesan HR.
- Zona waktu berasal dari lokasi penugasan: Asia/Jakarta, Asia/Makassar atau
  Asia/Jayapura. Perjalanan dinas perlu perubahan penugasan oleh HR. Radius
  tetap menerima semua lokasi kerja aktif; GPS tidak mengubah jadwal yang sudah
  ditetapkan. Waktu perangkat bukan sumber perhitungan keterlambatan.
- Selisih jam masuk dan kelebihan toleransi disimpan terpisah, dibulatkan ke atas
  ke menit. Masuk tepat di batas toleransi tidak ditandai terlambat.
- Istirahat hanya dikurangi sebesar irisan waktu kehadiran dengan jam istirahat.
  Masuk lebih awal tidak otomatis dihitung lembur. Jam kerja dalam jadwal maksimal
  8 jam; jam setelah jadwal merupakan kandidat lembur, pembayaran tetap mengikuti
  persetujuan. Tidak ada aturan pemotongan gaji keterlambatan baru.
- Lima jam setelah pulang, worker menutup sesi tanpa mengisi `clock_out` atau
  mengestimasi jam kerja. `auto_close_at` adalah batas; `auto_closed_at` adalah
  waktu worker memprosesnya. Status menjadi `required` (menunggu klarifikasi).
- Karyawan wajib mengirim alasan, jam pulang sebenarnya beserta tanggal, dan
  catatan. Pengajuan pending tidak dapat dikirim ulang. Setelah ditolak, dapat
  diperbaiki. Pengajuan tidak memblokir absensi shift berikutnya.
- Persetujuan HR menerapkan jam pulang aktual. Jika alasannya lembur, tindakan
  persetujuan juga membuat persetujuan lembur untuk payroll. Alasan lainnya tidak
  menghasilkan jam lembur. Jam pulang tidak boleh di masa depan atau tumpang
  tindih dengan sesi berikutnya. Payroll terkunci mencegah penerapan koreksi.
- Absensi lama tanpa snapshot jadwal tetap mengikuti perilaku lama; tidak ditutup
  otomatis memakai perkiraan jadwal. Selesaikan sesi lama sebelum transisi.

## Deployment

1. Deploy backend dengan dependency baru dan jalankan `alembic upgrade head`
   melalui alur migrasi deployment yang sudah ada. Migrasi terbaru:
   `m0b7c8d9e0f1` (setelah `l9a6b7c8d9e0`). Tidak mengubah isi absensi historis.
2. Jalankan `python scripts/generate_web_push_keys.py` satu kali di lingkungan
   tepercaya. Simpan `WEB_PUSH_PRIVATE_KEY`, `WEB_PUSH_PUBLIC_KEY`, serta
   `WEB_PUSH_SUBJECT=mailto:<email-administrator>` sebagai variabel backend.
   Gunakan pasangan berbeda untuk staging dan production. Jangan commit output.
3. Deploy frontend. Domain HTTPS dan proxy `/api` yang sudah ada tetap digunakan.
4. HR membuat template dan menetapkan jadwal sebelum karyawan melakukan clock-in
   baru. Fitur ini tidak menebak jadwal untuk karyawan yang belum ditugaskan.
5. Karyawan membuka Beranda/Absensi Saya dan menekan Aktifkan pengingat pulang.
   Di iPhone gunakan aplikasi yang dipasang ke Home Screen (iOS 16.4+).

Worker berjalan pada proses backend setiap sekitar 30 detik. Jaga setidaknya satu
instance tetap aktif (matikan sleep/serverless untuk backend jika digunakan).
Penutupan diproses pada putaran setelah deadline, bukan dijamin tepat detiknya.
Jika backend berhenti, sesi yang terlewat diproses saat kembali aktif. Reminder
yang sudah melewati jam pulang tidak dikirim ulang. Row locking dan delivery
outbox mencegah dua worker mengambil pekerjaan yang sama; retry jaringan tetap
bisa menghasilkan kiriman ulang, sehingga notifikasi memakai tag sesi yang sama.

Notifikasi in-app tetap tersedia jika kunci push belum diisi. Web Push membutuhkan
izin, koneksi internet, dan dukungan perangkat; tidak menjamin waktu penerimaan.
Service worker hanya menerima notifikasi, tidak menyimpan cache data login/API.
Logout menonaktifkan langganan pada perangkat tersebut.

## Validasi yang dilakukan

Peninjauan kode dan diff saja. Tidak menjalankan testing, build, migrasi, worker,
pengiriman notifikasi, atau mengubah database/deployment sesuai instruksi pengguna.
