# Distribusi Root CA

Tujuan: perangkat dapat memverifikasi tanda tangan tanpa peringatan
"unknown signer".

## 1. Unduh

- Root: `https://verif.stit-buntetpesantren.ac.id/ca/root.crt`
- Intermediate: `https://verif.stit-buntetpesantren.ac.id/ca/intermediate.crt`
- Fingerprint: `https://verif.stit-buntetpesantren.ac.id/ca`

Bandingkan fingerprint dengan yang diumumkan resmi sebelum memasang.

## 2. Windows

1. Klik dua kali `root.crt` → **Install Certificate**.
2. Pilih **Local Machine** → **Place all certificates in the following store**.
3. Pilih **Trusted Root Certification Authorities** → **OK**.

Untuk domain kampus, distribusikan lewat Group Policy:

```
Computer Configuration → Policies → Windows Settings → Security Settings
→ Public Key Policies → Trusted Root Certification Authorities
```

## 3. macOS

```bash
sudo security add-trusted-cert -d -r trustRoot \
  -k /Library/Keychains/System.keychain root.crt
```

## 4. Linux

```bash
sudo cp root.crt /usr/local/share/ca-certificates/stit-root-ca.crt
sudo update-ca-certificates
```

## 5. Adobe Acrobat / Reader

Adobe memakai AATL, bukan trust store OS. Untuk verifikasi lokal:

1. **Preferences → Signatures → Identities & Trusted Certificates → More**.
2. **Trusted Certificates → Import** → pilih `root.crt`.
3. Tandai sebagai trusted root.

Untuk perangkat kampus, distribusikan preferensi ini lewat GPO/Intune.

## 6. Verifikasi tanpa instalasi

Portal `https://verif.stit-buntetpesantren.ac.id` selalu menjadi sumber
kebenaran. Pengguna tidak perlu memasang sertifikat untuk memeriksa dokumen.

## 7. Catatan

- Jangan pernah menerima root CA dari sumber tidak resmi.
- Root CA internal tidak diakui Adobe secara default dan bukan PSrE.
- Jika root diganti, umumkan fingerprint baru dan tarik yang lama.
