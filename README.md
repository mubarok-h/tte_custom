# Mesin TTE STIT Buntet Pesantren

Mesin tanda tangan elektronik dokumen PDF berbasis **PAdES** dengan **QR verifikasi**,
menggunakan **CA internal** (self-managed, non-PSrE / non-AATL).

- Tanda tangan kriptografis menempel di PDF (bukan hanya di QR).
- Perubahan 1 byte pada PDF terdeteksi sebagai `INVALID`.
- QR mengarah ke `verif.stit-buntetpesantren.ac.id/v/<short_id>?t=<token>`.
- Revocation via CRL; audit log ber-hash-chain.
- Kunci dapat disimpan di **Nitrokey HSM 2** (PKCS#11).
- Timestamp RFC 3161 dari DigiCert/Sectigo (opsional, B-T/B-LT).

> **Status hukum:** ini TTE Tidak Tersertifikasi. Sah sebagai alat bukti
> sepanjang keaslian dan integritasnya dapat dibuktikan, tetapi bukan
> TTE Tersertifikasi PSrE. Jangan menyebut "PSrE" atau "AATL" pada dokumen.

---

## 1. Quickstart lokal

```bash
# 1. Dependensi
make venv

# 2. Buat CA dev + leaf Rektor + CRL
make bootstrap

# 3. Jalankan
cp .env.example .env
make dev
```

Buka:

- Portal: <http://localhost:8000/>
- Panel admin: <http://localhost:8000/admin> (masuk dengan `TTE_ADMIN_TOKEN`)
- Dokumentasi API: <http://localhost:8000/docs>
- Sertifikat CA: <http://localhost:8000/ca>

`make bootstrap` membuat:

```
ca/dev/root/            # root CA offline
ca/dev/intermediate/    # intermediate CA
ca/dev/leaves/          # leaf per pejabat
data/pki/root.crt
data/pki/intermediate.crt
data/pki/rektor.p12     # pass: changeit
data/pki/crl/stit.crl
data/pki/crl/root.crl
data/pki/tsa/           # root TSA (DigiCert)
```

---

## 2. Alur pemakaian

### Upload

```bash
curl -s -F "file=@surat.pdf" http://localhost:8000/api/v1/documents
```

Respons berisi `id`, `short_id`, `source_hash`, dan `verify_url`.

### Buat permintaan tanda tangan

```bash
curl -s -X POST http://localhost:8000/api/v1/sign-requests \
  -H 'Content-Type: application/json' \
  -d '{
    "document_id": "<id>",
    "signer": {
      "name": "Rektor STIT Buntet Pesantren",
      "jabatan": "Rektor",
      "nip": "1987654321",
      "email": "rektor@stit-buntetpesantren.ac.id"
    },
    "reason": "Pengesahan Dokumen Resmi"
  }'
```

### Consent + tanda tangan

```bash
curl -s -X POST http://localhost:8000/api/v1/sign-requests/<request_id>/consent \
  -H 'Content-Type: application/json' -d '{"consent": true}'

curl -s -X POST http://localhost:8000/api/v1/sign-requests/<request_id>/sign
```

### Verifikasi

```bash
# Lewat short_id + token
curl -s "http://localhost:8000/api/v1/verify/<short_id>?t=<token>"

# Upload PDF apa pun
curl -s -F "file=@signed.pdf" http://localhost:8000/api/v1/verify
```

### Unduh

```bash
curl -OJ "http://localhost:8000/api/v1/documents/<id>/download?t=<token>"
```

---

## 3. Sertifikat per pejabat

Saat penanda tangan ditambahkan lewat **Panel Admin → Penanda Tangan**, aplikasi
otomatis menerbitkan sertifikat leaf dan file P12 dari intermediate CA:

- disimpan di `data/pki/signers/<signer-id>/`;
- password P12 dienkripsi dengan `TTE_TOKEN_SECRET`;
- subject sertifikat memakai nama, jabatan, NIP, dan email penanda tangan;
- file sertifikat dan kunci dihapus ketika penanda tangan dihapus;
- penanda tangan lama tanpa sertifikat dapat memakai tombol
  **Terbitkan sertifikat** di menu Aksi.

Penerbitan otomatis memerlukan akses kunci intermediate CA di server
(`ca/dev/intermediate/private/intermediate.key` untuk dev). Bila CA privat
disimpan offline, terbitkan P12 secara manual:

```bash
make issue NAME="Dekan Fakultas Tarbiyah" NIP="19870001" EMAIL="dekan@stit-buntetpesantren.ac.id"
```

Hasil di `ca/dev/leaves/`. Salin `.p12` ke `data/pki/` lalu arahkan
`TTE_SIGNING_P12` ke file tersebut sebagai fallback global.

Untuk produksi dengan banyak pejabat, gunakan HSM (lihat bagian 5).

---

## 4. Revocation

```bash
make revoke CERT=ca/dev/leaves/dekan-fakultas-tarbiyah.crt
```

Perintah ini mencabut sertifikat dan memperbarui CRL. Portal akan menampilkan
status `REVOKED` setelah CRL baru dimuat.

CRL diterbitkan ulang otomatis? Tidak. Jadwalkan:

```cron
0 2 * * * cd /opt/tte && make crl
```

---

## 5. HSM Nitrokey HSM 2

Produksi wajib memakai HSM agar private key tidak menjadi file.

```bash
# Di host
sudo apt install opensc pcscd
sudo systemctl enable --now pcscd

# Cek token
pkcs11-tool --module /usr/lib/x86_64-linux-gnu/opensc-pkcs11.so -L
```

Konfigurasi `.env`:

```env
TTE_SIGNING_P12=
TTE_PKCS11_LIB=/usr/lib/x86_64-linux-gnu/opensc-pkcs11.so
TTE_PKCS11_KEY_LABEL=Rektor STIT Buntet Pesantren
TTE_PKCS11_CERT_LABEL=Rektor STIT Buntet Pesantren
TTE_PKCS11_PIN=123456
```

Docker: mount socket pcscd.

```yaml
volumes:
  - /run/pcscd/pcscd.comm:/run/pcscd/pcscd.comm
```

Catatan: Nitrokey HSM 2 nyaman untuk **≤30 penanda tangan**. Di atas itu,
pertimbangkan YubiHSM 2 (256 slot, FIPS).

---

## 6. Timestamp (TSA)

```env
TTE_TSA_ENABLED=true
TTE_TSA_REQUIRED=false
TTE_TSA_URLS=http://timestamp.digicert.com,http://timestamp.sectigo.com
TTE_EMBED_LTV=false   # true untuk B-LT (butuh root TSA di data/pki/tsa)
```

- `TTE_EMBED_LTV=false` → PAdES **B-T** (timestamp, tanpa DSS).
- `TTE_EMBED_LTV=true` → PAdES **B-LT** (chain + revocation info ditanam).
- `TTE_TSA_REQUIRED=true` → signing gagal bila semua TSA tidak tersedia.

Tanpa TSA, dokumen tetap ditandatangani sebagai **B-B**. Ini berguna untuk
operasi offline, tetapi validitas jangka panjang lebih lemah.

---

## 7. Docker

```bash
cp .env.example .env
docker compose up -d --build
```

Dengan Postgres (jangan mengubah `TTE_DATABASE_URL` lokal):

```bash
TTE_DOCKER_DATABASE_URL=postgresql+psycopg://tte:tte@postgres:5432/tte \
  docker compose --profile postgres up -d --build
```

Caddy akan menerbitkan TLS untuk `verif.stit-buntetpesantren.ac.id`
(Let's Encrypt **hanya** untuk HTTPS, bukan untuk PDF).

---

## 8. Test dan pemeliharaan

```bash
make test      # 12 test: flow, tamper, QR, multisign, audit
make audit     # periksa hash-chain audit log
make archive   # arsipkan dokumen yang melewati retensi 5 tahun
make crl       # terbitkan ulang CRL
```

Jadwalkan di cron:

```cron
0 2 * * * cd /opt/tte && make crl
0 3 * * * cd /opt/tte && make audit
0 4 * * * cd /opt/tte && make archive
```

---

## 9. Checklist produksi

- [ ] Ganti `TTE_TOKEN_SECRET` dengan nilai acak panjang.
- [ ] Set `TTE_REQUIRE_AUTH=true` dan ganti `TTE_ADMIN_TOKEN`.
- [ ] Set `TTE_VERIFY_BASE_URL=https://verif.stit-buntetpesantren.ac.id`.
- [ ] Aktifkan TSA: `TTE_TSA_ENABLED=true`, `TTE_EMBED_LTV=true`.
- [ ] Pindahkan kunci ke Nitrokey HSM 2 (bagian 5).
- [ ] Ganti `TTE_SIGNING_P12_PASSPHRASE` atau hapus file `.p12` dari server.
- [ ] Jalankan Caddy dengan domain produksi dan TLS valid.
- [ ] Jadwalkan `make crl`, `make audit`, `make archive`.
- [ ] Backup `data/` + database, uji restore.
- [ ] Publikasikan fingerprint root di `/ca`.

---

## 10. Struktur

```
tte_core/               # aplikasi FastAPI + modul signing/verification
  api/                  # signing, verification, portal, admin, letters, CA
  templates/            # halaman verifikasi, permohonan surat, panel admin
  static/sneat/         # aset template Sneat (MIT), disajikan lokal
  static/brand/         # logo resmi STIT Buntet Pesantren
ca/                     # openssl.cnf + skrip CA
  scripts/              # init-root, init-intermediate, issue-leaf, revoke, crl
  tsa-roots/            # root TSA publik (DigiCert)
db/migrations/          # skema SQL referensi
docs/                   # key ceremony, kebijakan, trust, threat model
tests/                  # pytest
```

Peran `signer`, `verifier`, dan `portal` saat ini berjalan dalam satu service
modular (`TTE_ROLES=all`). Untuk memisahkan, jalankan instance dengan
`TTE_ROLES=signer`, `TTE_ROLES=verifier`, atau `TTE_ROLES=portal`.

---

## 11. Keamanan

- Private key tidak boleh masuk repo, image, atau database.
- PIN/passphrase dari Docker secret atau environment terenkripsi.
- `TTE_REQUIRE_AUTH=true` dan ganti `TTE_ADMIN_TOKEN` di produksi.
- Batasi ukuran upload dan jumlah halaman.
- Backup `data/` dan database; uji restore.
- Root CA dibuat di mesin offline, backup 2 lokasi.
- Publikasikan fingerprint root di `/ca`.

---

## 12. Panel admin

Panel admin tersedia di `/admin` dan memakai mesin yang sama (bukan service
terpisah). Panel sudah **multi-user**: akun admin pertama dibuat otomatis dari
`TTE_ADMIN_USERNAME` / `TTE_ADMIN_TOKEN`, lalu password disimpan sebagai hash
PBKDF2-SHA256. Tambah operator dan ganti password lewat menu **Pengguna**.

```env
TTE_ADMIN_USERNAME=admin
TTE_ADMIN_TOKEN=ganti-token-admin
TTE_ADMIN_SESSION_HOURS=12
```

Fitur:

- Dashboard statistik dokumen, permohonan surat, penanda tangan, dan status sistem.
- **Permintaan surat**: antrean Diajukan → Diverifikasi → Disetujui → Terbit TTE,
  lengkap dengan penolakan beralasan.
- **Master data**: lembaga, fakultas, dan program studi beserta nama/jabatan ketua.
- **Pengguna**: tambah akun, atur role `admin` / `operator`, reset password,
  aktif/nonaktifkan akun.
- Unggah PDF, unduh PDF asli/TTE, dan salin tautan verifikasi.
- Kelola profil penanda tangan (tambah, aktif/nonaktif).
- Buat permintaan tanda tangan, catat consent, lalu tanda tangani dari UI.
- Audit log ber-hash-chain: lihat payload dan periksa integritas rantai.
- Pemeriksaan cepat dokumen memakai short ID + token.

Keamanan panel:

- Cookie sesi ditandatangani HMAC (`TTE_TOKEN_SECRET`), `HttpOnly`, `SameSite=Lax`,
  dan `Secure` bila URL publik memakai HTTPS.
- Semua form POST memakai token CSRF; login dibatasi 8 percobaan gagal per IP
  dalam 10 menit.
- Halaman admin mengirim `X-Frame-Options: DENY`, `noindex`, dan `Cache-Control: no-store`.
- Password akun disimpan dengan PBKDF2-SHA256 + salt; sesi berisi user id dan
  role, lalu divalidasi ulang ke database (akun nonaktif langsung ditolak).
- Hanya role `admin` yang dapat membuka menu Pengguna dan Master Data; operator
  dapat memproses surat, dokumen, penanda tangan, dan audit.
- Ganti password admin awal lewat menu **Pengguna**, dan ganti
  `TTE_TOKEN_SECRET` sebelum produksi. Panel menampilkan peringatan bila masih
  memakai nilai default.

Tampilan panel memakai **Sneat Free Bootstrap 5 Admin Template** dari
ThemeSelection (lisensi MIT) sebagai basis komponen, lalu diadaptasi ke bahasa
desain **SIAP! PPs UIN SGD** (kartu membulat, Plus Jakarta Sans, sidebar kartu,
statistik berwarna) serta palet resmi
**STIT Buntet Pesantren** (navy `#002147`, coral `#EE4A62`, teal `#1AB69D`,
oranye `#F8941F`, ungu `#8E56FF`). Logo institusi diambil dari situs resmi dan
disimpan lokal di `tte_core/static/brand/`. Aset tema disimpan lokal di
`tte_core/static/sneat/` dan tidak mengambil CDN, sehingga tetap berjalan
offline. Teks lisensi ada di `tte_core/static/sneat/LICENSE.md`.

> Panel admin tidak menggantikan kontrol akses API. Untuk produksi tetap set
> `TTE_REQUIRE_AUTH=true` dan batasi akses `/admin` di reverse proxy bila perlu.

---

## 13. Permohonan surat & master data

Alur publik:

- `/permohonan` — formulir permohonan surat akademik (identitas, data akademik,
  jenis surat, tujuan, keperluan).
- `/permohonan/status?q=<NIM atau nomor permohonan>` — lacak status dan riwayat.
- `/permohonan/validasi?no=<nomor surat>` — validasi keaslian nomor surat.

Alur admin:

- `/admin/letters` — antrean dan filter permohonan.
- `/admin/letters/<id>` — verifikasi, setujui + isi nomor surat, tolak, buat PDF
  dari format surat, lalu lanjutkan ke dokumen untuk TTE. Unggah PDF manual
  tetap tersedia sebagai cadangan.
- `/admin/master` — CRUD lembaga, fakultas, prodi, dan data ketuanya.
- `/admin/letter-templates` — CRUD format surat per jenis surat.
- `/admin/letterhead` — kop surat global (logo, alamat, kontak).
- `/admin/users` — manajemen akun multi-user.

Pada instalasi baru, master data awal dibuat otomatis: **STIT Buntet
Pesantren**, **Fakultas Tarbiyah**, serta prodi **BKPI** dan **MPI**. Nama
ketua/dekan/kaprodi dikosongkan dan dapat dilengkapi lewat panel admin.

Status permohonan: `submitted` → `verified` → `approved` → `signed`, atau
`rejected`. Setelah disetujui, PDF surat dibuat dari format surat dan terhubung
ke permohonan; proses tanda tangan memakai alur TTE yang sama. Setelah dokumen
ditandatangani, status permohonan menjadi `signed` dan nomor surat dapat
divalidasi publik.

### Format surat & kop

Lima format contoh dibuat otomatis (aktif kuliah, pengantar penelitian,
keterangan lulus, rekomendasi, keterangan lainnya) dan muncul sebagai pilihan
di `/permohonan`. Isi surat berupa HTML yang bisa diedit dengan pratinjau
langsung. Kop surat bersifat global dan dipakai seluruh format.

Keyword diambil dari data permohonan dan pejabat yang dipilih saat membuat PDF:

| Penulisan | Hasil |
|---|---|
| `[NAMA]` | kapital semua |
| `[nama]` | huruf kecil semua |
| `[Nama]` | awal kata kapital |
| `[blok_ttd]` | kota, tanggal, jabatan, nama, NIP |

Keyword yang tersedia: identitas pemohon (`nama`, `nim`, `tempat_lahir`,
`tanggal_lahir`, `email`, `telepon`, `alamat`), akademik (`lembaga`, `fakultas`,
`prodi`, `semester`, `tahun_akademik`), permohonan (`jenis_surat`, `tujuan`,
`keperluan`, `judul_penelitian`), surat (`nomor_surat`, `nomor_permohonan`,
`tanggal_surat`, `kota`, `tahun`), dan penanda tangan
(`nama_penandatangan`, `jabatan_penandatangan`, `nip_penandatangan`).

Tipografi surat diatur global di `/admin/letter-style`: jenis huruf (Times New
Roman, Arial, Georgia, Calibri, Liberation Serif, Noto Serif, DejaVu Serif),
ukuran teks, jarak baris, jarak paragraf, margin halaman, serta ukuran teks kop
(baris pertama, baris utama, baris alamat), serta jarak antara logo dan teks kop. Perubahan berlaku untuk surat
yang dibuat setelahnya; PDF yang sudah terbit tidak berubah.

### Penyimpanan Google Drive

Dokumen PDF dapat dicadangkan otomatis ke Google Drive. Pengaturan dilakukan di
**Sistem → Penyimpanan**:

1. Buat project di Google Cloud Console dan aktifkan **Google Drive API**.
2. Buat **Service Account**, lalu unduh kunci dalam format **JSON**.
3. Buat folder di Drive dan bagikan ke email service account sebagai **Editor**.
4. Isi **Folder ID** dan unggah JSON di halaman Penyimpanan, lalu aktifkan.
5. Gunakan **Uji koneksi Drive** untuk memastikan konfigurasi benar.

Selain folder global, Anda dapat menambahkan beberapa **folder bernama** di
halaman Penyimpanan dan memilihnya per format surat (kolom **Folder Drive** pada
editor format). Jika format tidak memilih folder, dipakai folder default. Setiap
format surat dapat diarahkan ke folder berbeda, misalnya surat keterangan,
surat penelitian, dan surat rekomendasi.

Setiap PDF asli dan PDF bertanda tangan diunggah ke folder yang sesuai; ID
berkas dan folder disimpan pada dokumen. Jika salinan lokal hilang, aplikasi mengunduhnya kembali
dari Drive saat dibutuhkan (unduhan, verifikasi, atau lampiran email). Saat
dokumen dihapus, berkas di Drive juga dihapus. Kegagalan unggah tidak
menggagalkan proses tanda tangan dan tercatat pada audit log
(`drive.upload_failed`). Kredensial disimpan terenkripsi dengan
`TTE_TOKEN_SECRET` dan tidak pernah ditampilkan kembali di antarmuka.

### Email otomatis (Gmail SMTP)

Konfigurasi email diatur dari panel admin: **Sistem → Email**. Tidak perlu
mengedit `.env` untuk pemakaian normal.

1. Aktifkan verifikasi 2 langkah pada akun Google.
2. Buat **App Password** 16 karakter di
   `myaccount.google.com/apppasswords` (kata sandi akun biasa tidak bisa dipakai).
3. Buka `/admin/email`, isi `smtp.gmail.com`, port `587`, alamat Gmail, dan App
   Password, lalu aktifkan pengiriman.
4. Simpan, kemudian gunakan **Kirim email uji**.

Halaman yang sama menampilkan status konfigurasi, riwayat 25 pengiriman terakhir
(tujuan, jenis, status, dan pesan galat), serta sakelar kapan email dikirim:

- **Saat disetujui** — pemberitahuan berisi nomor surat dan tautan lacak status.
- **Saat semua penandatangan selesai** — email dengan lampiran PDF bertanda
  tangan. Surat tidak dikirim saat PDF baru dibuat.

Tombol **Kirim ulang** tersedia di halaman detail permohonan. Kata sandi
disimpan terenkripsi dengan `TTE_TOKEN_SECRET` dan tidak pernah ditampilkan
kembali di antarmuka. Bila tabel `email_settings` masih kosong, nilai dari
`TTE_SMTP_*` pada `.env` dipakai sebagai isi awal. Kegagalan pengiriman tidak
menggagalkan persetujuan surat; penyebabnya dicatat pada riwayat email.

PDF dibuat dengan WeasyPrint pada kertas A4. Jika format memuat keyword yang
tidak dikenal, pembuatan PDF ditolak agar surat tidak tercetak dengan teks
`[keyword]` mentah. Pemohon lama tetap aman ketika format dihapus karena jenis
surat disimpan sebagai teks pada permohonan.

---

## 14. Batasan yang diketahui

- Adobe Reader menampilkan ikon "?" sampai root CA dipasang. Ini normal
  untuk CA internal. Portal tetap menjadi sumber verifikasi.
- Belum ada integrasi PSrE/BSrE. Engine menyimpan titik ekstensi untuk itu.
- OCR/QR decoding tidak dilakukan; verifikasi memakai signature, bukan QR.
- Multi-tanda tangan aktif secara default (`TTE_ENABLE_MULTISIGN=true`). Setiap
  tanda tangan menambah field `Signature1`, `Signature2`, dan seterusnya pada
  revisi PDF. Dapat dimatikan dengan `TTE_ENABLE_MULTISIGN=false`.
