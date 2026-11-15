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

```bash
make issue NAME="Dekan Fakultas Tarbiyah" NIP="19870001" EMAIL="dekan@stit-buntetpesantren.ac.id"
```

Hasil di `ca/dev/leaves/`. Salin `.p12` ke `data/pki/` lalu arahkan
`TTE_SIGNING_P12` ke file tersebut (satu signer aktif per proses).

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
  api/                  # router signing, portal, CA
  templates/            # halaman verifikasi
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

## 12. Batasan yang diketahui

- Adobe Reader menampilkan ikon "?" sampai root CA dipasang. Ini normal
  untuk CA internal. Portal tetap menjadi sumber verifikasi.
- Belum ada integrasi PSrE/BSrE. Engine menyimpan titik ekstensi untuk itu.
- OCR/QR decoding tidak dilakukan; verifikasi memakai signature, bukan QR.
- Multi-tanda tangan bersifat opsional (`TTE_ENABLE_MULTISIGN=true`).
