# PANDUAN MENJALANKAN DI PC LOKAL

Paket ini adalah salinan kerja proyek `tte_custom` (mesin TTE STIT Buntet Pesantren:
PAdES + QR verifikasi, CA internal). Semua yang dibutuhkan untuk langsung jalan ada
di dalam paket — tidak perlu clone repo, tidak perlu `make bootstrap`.

## Isi paket

| Path | Keterangan |
|---|---|
| `tte_core/` | kode aplikasi FastAPI (app, signing, verification, template halaman) |
| `ca/` | skrip CA (openssl) + `ca/dev/` **CA dev lengkap dengan kunci privat** |
| `data/pki/` | root.crt, intermediate.crt, CRL, `rektor.p12` (passphrase: `changeit`), root TSA |
| `data/tte.db` | SQLite contoh berisi beberapa dokumen uji (boleh dihapus) |
| `data/documents/` | berkas PDF contoh (original + signed) |
| `.env` | konfigurasi lokal (sama dengan `.env.example`, hanya placeholder) |
| `Dockerfile`, `docker-compose.yml` | jalur Docker bila lebih suka |
| `docs/`, `deploy/` | kebijakan, threat model, contoh unit Apache/systemd |

> **PENTING:** CA di dalam paket adalah **CA dev** (dibuat 15 Nov 2026, root
> `FF:77:E1:46:…`). Ini **bukan** CA yang dipakai produksi (`verif.stit-buntetpesantren.ac.id`
> memakai root `92:52:6C:79:…`). Karena kunci privat CA dev ikut di dalam zip, jangan
> paket ini diunggah ke repo publik / dibagikan bebas.

---

## A. Cara tercepat — Windows (PowerShell)

Butuh **Python 3.11–3.12** (3.13 juga jalan; hindari 3.14 karena sebagian wheel belum tersedia).

```powershell
cd tte_custom
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn tte_core.app:app --reload --host 127.0.0.1 --port 8000
```

Buka:
- Portal: <http://127.0.0.1:8000/>
- Panel admin: <http://127.0.0.1:8000/admin> (password: nilai `TTE_ADMIN_TOKEN`)
- Halaman periksa/verifikasi: <http://127.0.0.1:8000/>
- Swagger: <http://127.0.0.1:8000/docs>
- Sertifikat CA: <http://127.0.0.1:8000/ca>

Kalau PowerShell menolak aktivasi venv: jalankan sekali
`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`.

## B. Cara tercepat — Linux / macOS

```bash
cd tte_custom
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn tte_core.app:app --reload --host 127.0.0.1 --port 8000
```

Setara dengan `make venv && make dev` (Makefile disediakan; `make bootstrap` **tidak perlu**
karena CA dev sudah ada).

## C. Jalur Docker

```bash
docker compose up -d --build
```

Sama seperti di atas, tetapi `.env` dibaca apa adanya. Folder `./data` di-mount ke `/data`,
jadi dokumen lama tetap terbaca.

---

## D. Konfigurasi (.env)

Nilai dev yang sudah terisi: `TTE_DATA_DIR=./data`, `TTE_SIGNING_P12=./data/pki/rektor.p12`,
`TTE_SIGNING_P12_PASSPHRASE=changeit`, `TTE_INSTITUTION=STIT Buntet Pesantren`.

Yang biasanya diubah saat kerja lokal:

```
TTE_REQUIRE_AUTH=false        # matikan cek X-API-Key saat ngoprek
TTE_ADMIN_TOKEN=token-lokal   # kalau auth dibiarkan true, ini kuncinya
TTE_ADMIN_USERNAME=admin      # nama pengguna panel admin
TTE_ADMIN_SESSION_HOURS=12    # masa berlaku sesi admin
TTE_TSA_ENABLED=false         # true = butuh internet ke DigiCert/Sectigo
TTE_PUBLIC_BASE_URL=http://127.0.0.1:8000
TTE_VERIFY_BASE_URL=http://127.0.0.1:8000
```

`TTE_TOKEN_SECRET` jangan diganti kalau ingin tautan/QR pada `data/tte.db` tetap valid.

---

## E. Uji satu siklus tanda tangan (curl)

```bash
# kalau TTE_REQUIRE_AUTH=true, tambahkan header: -H "X-API-Key: <TTE_ADMIN_TOKEN>"
curl -F "file=@data/documents/<uuid>/original.pdf" http://127.0.0.1:8000/api/v1/documents
curl -X POST http://127.0.0.1:8000/api/v1/sign-requests \
     -H 'Content-Type: application/json' \
     -d '{"document_id":"<id>","signer":{"name":"Rektor","jabatan":"Rektor","nip":"1"},"reason":"Uji lokal"}'
curl -X POST http://127.0.0.1:8000/api/v1/sign-requests/<req_id>/consent \
     -H 'Content-Type: application/json' -d '{"consent":true}'
curl -X POST http://127.0.0.1:8000/api/v1/sign-requests/<req_id>/sign
```

Di PowerShell, `curl` adalah alias `Invoke-WebRequest` — pakai `curl.exe` agar sintaks di atas
berlaku.

## F. Menerbitkan penanda tangan baru

Cara termudah: tambahkan penanda tangan di **Panel Admin → Penanda Tangan**.
Sertifikat P12 diterbitkan otomatis di `data/pki/signers/<id>/` dan dihapus
ketika penanda tangan dihapus.

Secara manual:

```bash
bash ca/scripts/issue-leaf.sh "Nama Jabatan" "NIP" "email@contoh.id"
bash ca/scripts/gen-crl.sh          # perbarui CRL setelah revoke
```

Butuh `openssl` di PATH (Windows: Git Bash + OpenSSL, atau WSL). Skrip di `ca/scripts/`
sudah diberi izin eksekusi (`+x`) di paket ini — bila alat ekstrak Anda tidak
mempertahankan izin berkas, jalankan sekali `chmod +x ca/scripts/*.sh`.
Penerbitan manual menulis ke `ca/dev/leaves/`; salin `.p12` ke `data/pki/` dan
arahkan `TTE_SIGNING_P12` sebagai fallback global.

## G. Menjalankan test

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Hasil uji paket ini (17 Nov 2026, Python 3.13): **12 passed**, dan satu siklus nyata
di lokal — upload → sign-request → consent → sign → verify — menghasilkan
`pades_level: B-B`, `valid: true`, `status: VALID`.

---

## H. Panel admin (UI)

Buka <http://127.0.0.1:8000/admin>, masuk dengan pengguna `admin` (dapat diubah
lewat `TTE_ADMIN_USERNAME`) dan password dari `TTE_ADMIN_TOKEN`.

Dari panel ini seluruh siklus dapat dilakukan tanpa curl:

1. **Master Data** → isi lembaga, fakultas, program studi, dan nama ketuanya.
2. **Pengguna** → tambah akun operator / administrator.
3. **Permintaan Surat** → verifikasi, setujui + nomor surat, atau tolak.
4. Setelah disetujui, unggah PDF final lalu buka dokumen untuk proses TTE.
5. **Dokumen** → unggah PDF bila belum lewat permohonan.
6. **Penanda tangan** → tambah profil pejabat.
7. Buka detail dokumen → **Buat permintaan tanda tangan** → **Setujui** → **Tandatangani**.
8. Unduh **PDF TTE**, salin tautan verifikasi, dan pantau **Audit**.

Halaman publik untuk mahasiswa:

- `/permohonan` — ajukan surat.
- `/permohonan/status` — lacak dengan NIM / nomor permohonan.
- `/permohonan/validasi` — validasi nomor surat.

Akun admin pertama dibuat dari `TTE_ADMIN_USERNAME` / `TTE_ADMIN_TOKEN`.
Setelah masuk, ganti password di menu **Pengguna**; token hanya dipakai untuk
seed akun pertama.

Panel memakai cookie sesi bertanda tangan dan token CSRF. Tampilannya memakai
template Sneat (MIT) yang asetnya disimpan lokal di `tte_core/static/sneat/`,
diadaptasi ke gaya SIAP! PPs UIN SGD dan palet resmi STIT Buntet Pesantren
(navy–coral–teal). Bila masih memakai token default, panel menampilkan
peringatan; ganti `TTE_ADMIN_TOKEN` dan `TTE_TOKEN_SECRET` sebelum dipakai di
jaringan nyata.

## Catatan penting

1. **Satu deployment = satu `TTE_SIGNING_P12`.** Nama penanda tangan yang tampil di PDF dan
   halaman verifikasi diambil dari **subjek sertifikat**, bukan dari payload permintaan tanda tangan.
2. **QR jangan dibuat sebelum sign.** pyhanko menempelkan QR + teks "Ditandatangani secara
   elektronik oleh …" saat proses sign; menyisipkan QR lebih dulu mengubah hash sehingga `verify`
   jadi INVALID.
3. **`verify` di lokal hanya mempercayai root CA dev.** PDF yang ditandatangani instalasi
   produksi akan tampil `intact: true, trusted: false` → INVALID. Itu benar, bukan kerusakan.
4. **CRL berumur ±30 hari.** Setelah lewat, verifikasi dokumen lama bisa jatuh ke tidak sah —
   jalankan `bash ca/scripts/gen-crl.sh`.
5. Jangan menyebut hasil ini "tersertifikasi PSrE/AATL". Ini TTE internal institusi.
