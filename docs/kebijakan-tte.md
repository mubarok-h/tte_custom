# Kebijakan TTE Internal STIT Buntet Pesantren (ringkas)

Dokumen ini adalah CP/CPS ringkas. Versi lengkap harus disahkan melalui SK
Rektor sebelum digunakan untuk dokumen resmi.

## 1. Ruang lingkup

- Dokumen PDF internal: surat tugas, surat keterangan, pengesahan, dan
  dokumen akademik lain yang ditetapkan Rektor.
- Penanda tangan: Rektor, Dekan, Kepala Unit, dan pejabat yang ditunjuk.
- CA dioperasikan oleh Pusat Data STIT Buntet Pesantren.

## 2. Status

Sertifikat adalah **Sertifikat Elektronik Internal** (non-PSrE). Tanda tangan
yang dihasilkan adalah **TTE Tidak Tersertifikasi** sesuai UU ITE dan
PP 71/2019. Dokumen tidak boleh menyebut "PSrE", "AATL", atau "tersertifikasi".

## 3. Identitas

- Setiap leaf diterbitkan untuk satu orang.
- Subject memuat CN (nama), OU/O (institusi), serialNumber (NIP), dan email.
- Penerbitan dilakukan atas permintaan tertulis pejabat berwenang.
- Pusat Data menerapkan maker/checker.

## 4. Siklus hidup sertifikat

| Tahap | Ketentuan |
|---|---|
| Penerbitan | Setelah verifikasi identitas dan persetujuan |
| Masa berlaku | Leaf 1–2 tahun |
| Revocation | Segera setelah kunci hilang/bocor atau pejabat berhenti |
| Publikasi | CRL di `/crl/stit.crl`, root di `/ca/root.crt` |

## 5. Kewajiban penanda tangan

- Menjaga kerahasiaan PIN/passphrase.
- Tidak mendelegasikan penggunaan kunci tanpa pencatatan.
- Melaporkan dugaan kompromi ke Pusat Data.

## 6. Consent dan audit

- Setiap penandatanganan memerlukan persetujuan eksplisit (consent).
- Waktu, IP, dan identitas penanda tangan dicatat.
- Audit log ber-hash-chain diperiksa berkala.

## 7. Retensi

- Dokumen disimpan **5 tahun** sejak tanggal dokumen.
- Setelah 5 tahun: dipindahkan ke arsip dingin atau dimusnahkan sesuai jadwal.
- Sebelum leaf/TSA kedaluwarsa, lakukan re-timestamp/arsip tanda tangan
  (archive timestamp belum diotomatisasi; jalankan prosedur manual).
- Pemusnahan dicatat di audit log.

## 8. Verifikasi

- Verifikasi utama melalui `verif.stit-buntetpesantren.ac.id`.
- Root CA dapat diunduh dan dipasang untuk verifikasi lokal.
- Status yang mungkin: `VALID`, `INVALID`, `REVOKED`, `UNSIGNED`, `UNKNOWN`.

## 9. Insiden

- Kunci bocor: cabut leaf, terbitkan CRL baru, umumkan bila perlu.
- Root bocor: hentikan penerbitan, buat root baru, migrasi dokumen.
- Insiden dicatat dan dilaporkan ke pimpinan.
