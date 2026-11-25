# Verify your Vokter download

Every Vokter release ships three extra files next to the `.deb` / `.AppImage`:
`SHA256SUMS` (the checksums), `SHA256SUMS.asc` (a GPG signature of that file),
and the signing key's fingerprint pinned here and in the repo. Verifying takes
four commands and proves two things: the checksum list is really ours, and your
download matches it byte for byte.

## 1. Import the Vokter release public key (once)

```
curl -LO https://vokterai.com/vokter-release-pubkey.asc
gpg --import vokter-release-pubkey.asc
```

(The same file is in the repo root: `vokter-release-pubkey.asc`.)

## 2. Check the fingerprint (once — this is the step that matters)

```
gpg --fingerprint "Vokter Releases"
```

It must read, exactly:

```
775D 4008 FB7D 0EE1 D369  BF17 4F2E 21E6 78C2 4E4B
```

If it differs in any character, STOP: you imported someone else's key. The
fingerprint is pinned in this document, in the public repo, and on
vokterai.com — cross-check at least two sources you trust.

## 3. Verify the checksum file's signature

In the folder where you downloaded the release files:

```
gpg --verify SHA256SUMS.asc SHA256SUMS
```

Expect `Good signature from "Vokter Releases ..."`. A warning like
`This key is not certified with a trusted signature` is normal — it only means
you haven't personally marked the key as trusted; the fingerprint check in
step 2 is what establishes trust here. `BAD signature` means the file was
tampered with: delete everything and re-download.

## 4. Verify your download against the signed checksums

```
sha256sum -c SHA256SUMS --ignore-missing
```

Expect `vokter-desktop_<version>_amd64.deb: OK`. Anything else — wrong size,
`FAILED`, missing — means the artifact is not the one we signed: do not
install it.

That's it. Steps 3–4 repeat per release; steps 1–2 only on first use (or if we
ever rotate the key, which we would announce signed by the old one).
