# Instalasi di Mesin Baru

Panduan ini untuk memasang mesin TTE di server kedua (on-prem, Ubuntu/Debian).

Ada dua skenario:

| Skenario | Yang dibutuhkan |
|---|---|
| **Instalasi baru** (belum ada dokumen ditandatangani) | CA baru dibuat di mesin ini |
| **Migrasi** (sudah ada dokumen & QR beredar) | CA, database, storage, dan `TTE_TOKEN_SECRET` lama harus ikut |

> Jangan pernah menyalin `ca/dev/root/private/root.key` ke server online.
> Root key cukup disimpan offline.

---

## 1. Prasyarat

```bash
sudo apt update
sudo apt install -y git python3 python3-venv openssl curl rsync
```

Opsional:

```bash
# Docker
sudo apt install -y docker.io docker-compose-plugin
sudo usermod -aG docker "$USER"   # logout/login setelah ini

# HSM Nitrokey
sudo apt install -y opensc pcscd libpcsclite1
sudo systemctl enable --now pcscd
```

Cek:

```bash
python3 --version     # minimal 3.11
openssl version
docker --version      # opsional
```

---

## 2. Ambil kode dari GitHub

Repo bersifat **private**. Pilih salah satu:

### Dengan GitHub CLI

```bash
gh auth login
gh repo clone mubarok-h/tte_custom
cd tte_custom
```

### Dengan SSH

```bash
ssh-keygen -t ed25519 -C "tte-server"
cat ~/.ssh/id_ed25519.pub
# tambahkan ke GitHub → Settings → SSH and GPG keys

git clone git@github.com:mubarok-h/tte_custom.git
cd tte_custom
```

### Dengan Personal Access Token

```bash
git clone https://<TOKEN>@github.com/mubarok-h/tte_custom.git
cd tte_custom
```

---

## 3. Instalasi dependensi

```bash
make venv
```

Atau manual:

```bash
python3 -m venv --without-pip .venv
curl -fsSL https://bootstrap.pypa.io/get-pip.py | .venv/bin/python -q
.venv/bin/pip install -r requirements-dev.txt
```

---

## 4. Siapkan CA

### A. Instalasi baru

Buat CA baru di mesin ini:

```bash
make bootstrap
```

Ini menghasilkan:

```
data/pki/root.crt
data/pki/intermediate.crt
data/pki/rektor.p12
data/pki/crl/stit.crl
data/pki/crl/root.crl
data/pki/tsa/
```

Untuk produksi, jangan pakai `make bootstrap` apa adanya. Lakukan
[key ceremony](key-ceremony.md) di mesin offline, lalu pindahkan hanya
intermediate + leaf ke server ini.

### B. Migrasi dari server lama

Yang **wajib** dibawa:

```bash
# Dari server lama
rsync -av user@server-lama:/path/tte_custom/data/ ./data/
rsync -av user@server-lama:/path/tte_custom/ca/dev/intermediate/ ./ca/dev/intermediate/

# Root private key TIDAK boleh ikut ke server online.
```

Yang **tidak boleh** dibawa:

```
ca/dev/root/private/root.key
```

Setelah migrasi, pastikan:

- `data/pki/root.crt`, `intermediate.crt`, `crl/` ada.
- `data/documents/` dan `data/tte.db` ada.
- `TTE_TOKEN_SECRET` **sama** dengan server lama (lihat bagian 5).

Jika `TTE_TOKEN_SECRET` diganti, semua QR/link verifikasi lama menjadi
`403` karena token dihitung dari secret tersebut.

---

## 5. Konfigurasi `.env`

```bash
cp .env.example .env
```

Edit `.env`:

```env
TTE_ROLES=all
TTE_INSTITUTION=STIT Buntet Pesantren
TTE_LOCATION=Buntet Pesantren
TTE_PUBLIC_BASE_URL=https://verif.stit-buntetpesantren.ac.id
TTE_VERIFY_BASE_URL=https://verif.stit-buntetpesantren.ac.id

TTE_DATABASE_URL=sqlite:///./data/tte.db
TTE_DATA_DIR=./data

TTE_TOKEN_SECRET=<hasil openssl rand -hex 32>
TTE_SIGNING_P12=./data/pki/rektor.p12
TTE_SIGNING_P12_PASSPHRASE=<passphrase p12>

TTE_ROOT_CA_PATH=./data/pki/root.crt
TTE_INTERMEDIATE_CA_PATH=./data/pki/intermediate.crt
TTE_CRL_PATH=./data/pki/crl/stit.crl
TTE_ROOT_CRL_PATH=./data/pki/crl/root.crl
TTE_TSA_ROOTS_DIR=./data/pki/tsa

TTE_TSA_ENABLED=true
TTE_TSA_REQUIRED=false
TTE_EMBED_LTV=true
TTE_TSA_URLS=http://timestamp.digicert.com,http://timestamp.sectigo.com

TTE_RETENTION_YEARS=5
TTE_ENABLE_MULTISIGN=false

TTE_REQUIRE_AUTH=true
TTE_ADMIN_TOKEN=<hasil openssl rand -hex 24>
```

Generate secret:

```bash
openssl rand -hex 32   # TTE_TOKEN_SECRET
openssl rand -hex 24   # TTE_ADMIN_TOKEN
```

Amankan file:

```bash
chmod 600 .env
chmod 700 data/pki
chmod 600 data/pki/*.p12
```

---

## 6. Jalankan

### A. Langsung (uji coba)

```bash
make dev
```

Buka `http://IP-SERVER:8000`.

### B. Systemd (permanen)

```bash
sudo cp deploy/tte.service /etc/systemd/system/tte.service
sudo nano /etc/systemd/system/tte.service
# sesuaikan User= dan WorkingDirectory=

sudo systemctl daemon-reload
sudo systemctl enable --now tte
sudo systemctl status tte
journalctl -u tte -f
```

### C. Docker

```bash
docker compose up -d --build
```

Jika port 80/443 sudah dipakai Apache, jalankan API saja:

```bash
docker compose up -d --build api
```

Lalu pasang reverse proxy Apache (bagian 7).

---

## 7. Domain dan HTTPS

Tambahkan DNS **A record**:

```
verif.stit-buntetpesantren.ac.id  ->  IP-SERVER
```

### Apache reverse proxy

```bash
sudo cp deploy/apache-verif.conf /etc/apache2/sites-available/verif.conf
sudo a2enmod proxy proxy_http headers
sudo a2ensite verif
sudo apache2ctl configtest
sudo systemctl reload apache2
```

HTTPS:

```bash
sudo apt install -y certbot python3-certbot-apache
sudo certbot --apache -d verif.stit-buntetpesantren.ac.id
```

### Caddy (jika port 80/443 bebas)

```bash
docker compose up -d --build
```

Caddy akan mengurus TLS otomatis.

---

## 8. HSM (opsional, disarankan untuk produksi)

```bash
pkcs11-tool --module /usr/lib/x86_64-linux-gnu/opensc-pkcs11.so -L
```

Set di `.env`:

```env
TTE_SIGNING_P12=
TTE_PKCS11_LIB=/usr/lib/x86_64-linux-gnu/opensc-pkcs11.so
TTE_PKCS11_KEY_LABEL=Rektor STIT Buntet Pesantren
TTE_PKCS11_CERT_LABEL=Rektor STIT Buntet Pesantren
TTE_PKCS11_PIN=<PIN HSM>
```

Docker: mount socket pcscd.

```yaml
volumes:
  - /run/pcscd/pcscd.comm:/run/pcscd/pcscd.comm
```

---

## 9. Verifikasi instalasi

```bash
curl -s http://127.0.0.1:8000/api/v1/health
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/ca/root.crt
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/crl/stit.crl
```

Uji tanda tangan:

```bash
# upload
curl -s -F "file=@contoh.pdf" http://127.0.0.1:8000/api/v1/documents

# buat request
curl -s -X POST http://127.0.0.1:8000/api/v1/sign-requests \
  -H 'Content-Type: application/json' \
  -d '{"document_id":"<id>","signer":{"name":"Rektor","jabatan":"Rektor","nip":"1987654321"}}'

# consent + sign
curl -s -X POST http://127.0.0.1:8000/api/v1/sign-requests/<req>/consent \
  -H 'Content-Type: application/json' -d '{"consent":true}'
curl -s -X POST http://127.0.0.1:8000/api/v1/sign-requests/<req>/sign
```

Jika `TTE_REQUIRE_AUTH=true`, tambahkan header:

```bash
-H "X-API-Key: $TTE_ADMIN_TOKEN"
```

---

## 10. Firewall

```bash
# akses langsung
sudo ufw allow 8000/tcp

# kalau lewat reverse proxy
sudo ufw allow 80,443/tcp
```

Jangan buka 8000 ke publik jika sudah memakai reverse proxy; batasi ke
`127.0.0.1` atau jaringan internal.

---

## 11. Backup dan pemeliharaan

Backup:

```bash
tar czf tte-backup-$(date +%F).tar.gz data/ .env
```

Simpan terenkripsi di lokasi terpisah. Uji restore minimal sebulan sekali.

Cron:

```cron
0 2 * * * cd /opt/tte && make crl
0 3 * * * cd /opt/tte && make audit
0 4 * * * cd /opt/tte && make archive
```

---

## 12. Troubleshooting

| Gejala | Penyebab | Solusi |
|---|---|---|
| `http://localhost` menampilkan phpMyAdmin | Port 80 dipakai Apache | Buka `http://localhost:8000` |
| `Connection refused` | Service belum jalan | `systemctl status tte` / `make dev` |
| `403` di `/v/...` | Token tidak ada/salah | Gunakan URL dari hasil signing (`?t=...`) |
| QR lama jadi `403` | `TTE_TOKEN_SECRET` berubah | Kembalikan secret lama |
| Tanda tangan lama `INVALID` | CA berbeda | Migrasikan `data/pki` dan `ca/dev/intermediate` |
| Adobe menampilkan "?" | Root belum dipasang | Pasang `root.crt` atau verifikasi via portal |
| `no issuer matching` saat TSA | Root TSA belum ada | Pastikan `data/pki/tsa/*.crt` ada |
| Docker permission denied | User belum di grup docker | `sudo usermod -aG docker $USER` lalu re-login |
