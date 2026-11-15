# Key Ceremony — Root CA STIT Buntet Pesantren

Dokumen ini mencatat prosedur pembuatan dan penyimpanan root CA. Root CA
hanya dibuat sekali dan tidak boleh berada di server online.

## 1. Persiapan

- Mesin air-gapped (tanpa jaringan), atau mesin yang dimatikan setelahnya.
- Media penyimpanan baru (USB) untuk backup.
- Dua orang saksi dari Pusat Data.
- `ca/openssl.cnf` dan `ca/scripts/` dari repo (hash commit dicatat).

## 2. Pembuatan

```bash
export DEV_DIR=/media/offline/stit-ca
bash ca/scripts/init-root.sh
bash ca/scripts/init-intermediate.sh
```

Catat:

- Waktu (UTC)
- Peserta
- Fingerprint SHA-256 root
- Fingerprint SHA-256 intermediate
- Lokasi backup 1 dan 2
- Hash commit repo

## 3. Backup

1. Salin `root/private/root.key` dan `root/certs/root.crt` ke dua USB berbeda.
2. Enkripsi dengan passphrase kuat (mis. `age` atau LUKS).
3. Simpan di dua lokasi fisik berbeda.
4. Uji restore di mesin terpisah sebelum segel.

## 4. Setelah ceremony

- Root key tidak boleh disalin ke server aplikasi.
- Intermediate key dipindahkan ke HSM (Nitrokey HSM 2) atau file terenkripsi.
- Fingerprint root dipublikasikan di `/ca`.
- Berita acara ditandatangani peserta dan disimpan di arsip.

## 5. Rotasi

| Komponen | Masa berlaku | Tindakan |
|---|---|---|
| Root | 10 tahun | Buat root baru, cross-sign bila perlu |
| Intermediate | 5 tahun | Rotasi terjadwal, terbitkan ulang leaf |
| Leaf | 1–2 tahun | Rotasi sebelum expiry |

Dokumen lama tetap valid karena B-LT menyimpan chain + timestamp, bukan
bergantung pada leaf yang masih aktif.
