# Threat Model (ringkas)

## Aset

- Private key intermediate dan leaf.
- Root key offline.
- Dokumen asli dan bertanda tangan.
- Database metadata dan audit log.
- Token verifikasi.

## Ancaman dan mitigasi

| Ancaman | Dampak | Mitigasi |
|---|---|---|
| Pencurian private key | Pemalsuan tanda tangan | HSM, PIN, akses terbatas, rotasi, CRL |
| Modifikasi PDF | Pemalsuan isi | PAdES menutup seluruh byte; verifikasi ulang |
| QR ditempel ke dokumen lain | Salah arah verifikasi | Verifikasi signature, bukan QR; token terikat dokumen |
| Token ditebak | Enumerasi dokumen | HMAC 64-bit, rate limit, `no-store` |
| Replay consent | Tanda tangan tanpa izin | Consent + auth + audit + idempotency |
| TSA down | Signing gagal | Fallback list; mode B-B bila tidak wajib |
| CRL tidak tersedia | Status revocation tidak diketahui | Cache CRL, publikasi statis, monitoring |
| Database dibobol | Metadata bocor | Enkripsi at-rest, least privilege, backup |
| Audit log dihapus | Kehilangan bukti | Hash chain + backup + verifikasi berkala |
| PDF berbahaya | RCE | Batas ukuran/halaman, parser terisolasi, antivirus |
| Data pribadi di portal | Pelanggaran PDP | Tampilkan minimal, token acak, `no-store` |

## Residual risk

- Root key disimpan offline di media terenkripsi; risiko fisik tetap ada.
- Nitrokey HSM 2 bukan FIPS tingkat produk; untuk kebutuhan FIPS gunakan
  YubiHSM 2.
- CA internal tidak dipercaya Adobe secara default.
- TTE tidak tersertifikasi memiliki kekuatan pembuktian yang lebih lemah
  daripada TTE PSrE.
