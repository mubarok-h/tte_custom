# Deploy lokal di server STIT

## 1. Jalankan sekarang (tanpa systemd)

```bash
cd /home/mubarok/Documents/tte_custom
make dev
```

Buka **http://localhost:8000** atau **http://192.168.1.99:8000**.
Jangan buka `http://localhost` (port 80 dipakai Apache/phpMyAdmin).

## 2. Jalankan permanen dengan systemd

```bash
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

Saat ini `verif.stit-buntetpesantren.ac.id` **belum punya DNS record**.
Tambahkan A record ke IP server (mis. `192.168.1.99` untuk LAN atau IP publik),
lalu pasang reverse proxy Apache:

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

- Port 80/443 sudah dipakai Apache. Jangan jalankan Caddy di port yang sama.
- Jika domain hanya untuk LAN, tambahkan di `/etc/hosts` klien:
  `192.168.1.99 verif.stit-buntetpesantren.ac.id`
- Untuk produksi, gunakan `TTE_VERIFY_BASE_URL=https://verif.stit-buntetpesantren.ac.id`
  agar QR mengarah ke domain yang benar.
