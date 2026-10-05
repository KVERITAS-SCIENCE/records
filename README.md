# K-Veritas Records

Published K-Veritas records. Pages: [kveritas.org/records](https://kveritas.org/records)

## Layout

```
index/<yymm>.json                     records published that month
records/<yymm>/<id>/v<n>/
  metadata.yaml                       title, authors, abstract, file hashes
  reports/r<k>.pdf                    sealed reports, untouched
```

Code bundles: attached to the GitHub Release `<id>v<n>`.

## Verify

```
kveritas verify r1.pdf --bundle r1.kvbundle.zip
```

or upload both at [kveritas.org/verify](https://kveritas.org/verify).
