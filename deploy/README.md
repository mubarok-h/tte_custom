# Deploy lokal di server STIT

## 1. Jalankan sekarang (tanpa systemd)

```bash
cd /opt/tte
make dev
```

Buka **http://localhost:8000** atau **http://IP-SERVER:8000**.
Jangan buka `http://localhost` jika port 80 dipakai Apache/phpMyAdmin.

## 2. Jalankan permanen dengan systemd

```bash
sudo useradd -r -s /usr/sbin/nologin tte
sudo mkdir -p /opt/tte
sudo chown -R tte:tte /opt/tte

sudo cp deploy/tte.service /etc/systemd/system/tte.service
sudo systemctl daemon-reload
sudo systemctl enable --now tte
sudo systemctl status tte
```

Log:

```bash
journalctl -u tte -f
```

## 3. Akses lewat domain

Tambahkan A record `verif.stit-buntetpesantren.ac.id` ke IP server
(`IP-SERVER` untuk LAN atau IP publik), lalu pasang reverse proxy Apache:

```bash
sudo cp deploy/apache-verif.conf /etc/apache2/sites-available/verif.conf
sudo a2enmod proxy proxy_http headers
sudo a2ensite verif
sudo apache2ctl configtest
sudo systemctl reload apache2
```

Untuk HTTPS:

```bash
sudo apt install certbot python3-certbot-apache
sudo certbot --apache -d verif.stit-buntetpesantren.ac.id
```

Catatan:

- Jika port 80/443 sudah dipakai Apache, jangan jalankan Caddy di port yang sama.
- Jika domain hanya untuk LAN, tambahkan di `/etc/hosts` klien:
  `<IP-SERVER> verif.stit-buntetpesantren.ac.id`
- Untuk produksi, gunakan `TTE_VERIFY_BASE_URL=https://verif.stit-buntetpesantren.ac.id`
  agar QR mengarah ke domain yang benar.
