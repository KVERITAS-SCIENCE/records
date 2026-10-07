# K-Veritas Records

Published experiments, each sealed to the code, hardware and time that produced it. Pages:
[kveritas.org/records](https://kveritas.org/records)

## Submit

[Open a submission issue](https://github.com/KVERITAS-SCIENCE/records/issues/new?template=submit.yml). Attach the sealed reports and their code bundles.

Before submitting:

```
kveritas init --disclosure open
kveritas run -- <command>        # every run, failed ones included
kveritas seal
```

Submit `report.pdf` and `report.pdf.kvbundle.zip`. Never `report.pdf.provkey.json`.

## Rules

- Reports sealed through the K-Veritas server, not `--local`.
- Open disclosure: every report has its code bundle.
- Every run anchor intact.
- One record per paper or project; up to 50 reports.
- A report belongs to one record only.
- Bundles hold no secrets and nothing you may not publish.
- Submitted by you, or by you for the authors with their consent.
- Purpose: official implementation, independent reproduction, or other. A reproduction links the
  original code.
- Type of work: research paper, thesis, course assignment, benchmark or leaderboard entry, artifact
  evaluation, other. A course assignment needs the instructor's permission to publish.
- Field: any discipline, not only computer science.
- Paper link and code repository: optional, can be added later.

## Process

1. **Automatic checks** run on the issue: each report verifies, each bundle matches the hash its report
   signed, no report is already published, the form is complete.
2. **Failed check: rejected.** The issue is closed with the reason. Fix it and open a new issue.
3. **Passed check: review.** A maintainer checks the artifact, never the science: names fit the
   submitter, title and abstract describe the work, licence stated, no withheld files left unexplained.
4. **Approved: published.** The record gets its ID and page, the code bundles go to a Release, and the
   issue is closed with the link.

Editing an open issue reruns the checks.

## Update

[Open an update issue](https://github.com/KVERITAS-SCIENCE/records/issues/new?template=update.yml) with the record ID. Only the original
submitter or a maintainer can update. Same checks and review.

- **Reports added or removed: new version.** Earlier versions stay published.
- **Anything else: correction.** Title, authors, abstract, tags, licence, purpose, type, field, paper
  or code link. Applied to the current version in place, logged under `corrections` in
  `metadata.yaml`, no new version.

## Records

- ID `kv:YYMM.NNNNNvV`: month published, sequence, version. Never reused.
- Every number comes from the signed reports. Title, authors and abstract come from the submitter,
  reviewed for completeness.
- `record.pdf` is an unsigned cover page; its SHA-256 is in `metadata.yaml`. The sealed reports are
  authoritative.
- Listed, not endorsed.
- Reports never change; changing them makes a new version. Descriptive fields are corrected in place,
  each correction logged. Retractions stay visible with the reason. Takedowns only for legal problems
  or leaked secrets.
- Record pages CC-BY 4.0; code under the licence the submitter declared.

## Layout

```
index/<yymm>.json                     records published that month
records/<yymm>/<id>/v<n>/
  metadata.yaml                       title, authors, submitter, purpose, links, file hashes, corrections
  record.pdf                          cover page
  reports/r<k>.pdf                    sealed reports, untouched
```

Code bundles: on the Release `<id>v<n>`.

## Verify

```
kveritas verify r1.pdf --bundle r1.kvbundle.zip
```

or upload both at [kveritas.org/verify](https://kveritas.org/verify).
