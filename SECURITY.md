# Security Policy

## Melaporkan kerentanan

Jangan membuka issue publik untuk kerentanan. Kirim laporan ke:

```
security@stit-buntetpesantren.ac.id
```

Sertakan langkah reproduksi, dampak, dan versi/commit yang terpengaruh.

## Yang tidak boleh masuk repo

- Private key (root, intermediate, leaf), `.p12`, `.key`, `.pem`.
- File `.env` berisi secret nyata.
- Database dan dokumen pengguna (`data/`).
- PIN/passphrase HSM.
- Backup CA.

Semua sudah di-`.gitignore`. Jika ada yang tidak sengaja ter-commit:

1. Cabut/rotasi kunci atau secret tersebut segera.
2. Terbitkan CRL baru jika kunci sertifikat bocor.
3. Bersihkan riwayat git (`git filter-repo`) dan force-push.
4. Anggap secret lama sudah bocor meskipun commit dihapus.

## Default yang wajib diganti sebelum produksi

| Variabel | Default | Risiko |
|---|---|---|
| `TTE_TOKEN_SECRET` | `dev-secret-change-me` | Token verifikasi bisa dihitung |
| `TTE_ADMIN_TOKEN` | `dev-admin-token` | API signing bisa diakses |
| `TTE_SIGNING_P12_PASSPHRASE` | `changeit` | Kunci PKCS#12 bisa dibuka |
| `POSTGRES_PASSWORD` | `tte` | Database bisa diakses |

Aplikasi akan menulis peringatan di log jika default terdeteksi.

## Hardening minimum

- `TTE_REQUIRE_AUTH=true`.
- HTTPS di reverse proxy.
- Port aplikasi hanya di localhost/internal.
- HSM untuk private key produksi.
- Backup terenkripsi dan uji restore.
- `make audit` dan `make crl` terjadwal.
- Pembatasan ukuran upload dan rate limit di reverse proxy.

## Catatan tentang repo publik

Repo ini dapat dipublikasikan tanpa private key, tetapi tetap memuat:

- Nama institusi dan domain.
- Arsitektur, endpoint, dan kebijakan internal.
- Nilai default pengembangan.

Itu bukan rahasia, tetapi meningkatkan pengetahuan penyerang. Jangan pernah
mengandalkan kerahasiaan kode sebagai kontrol keamanan.
