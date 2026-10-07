"""Check and publish K-Veritas records from GitHub issues.

    records.py check     validate the issue in ISSUE_* env vars; write the comment to $OUT/comment.md
    records.py publish   same checks, then publish into this repo
    records.py cover ID  regenerate record.pdf for a published version

Issue text is untrusted: it is only parsed here, never passed to a shell.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(".").resolve()
OUT = Path(os.environ.get("OUT", "/tmp/records-out")).resolve()
KV = Path(os.environ.get("KVERITAS_BIN", "kveritas")).resolve()
REPO = os.environ.get("GITHUB_REPOSITORY", "KVERITAS-SCIENCE/records")
BEGIN, END = b"%%KVERITAS_SEAL_BEGIN%%", b"%%KVERITAS_SEAL_END%%"
ATTACHMENT = re.compile(r"\[([^\]\n]+)\]\((https://github\.com/user-attachments/(?:files|assets)/[A-Za-z0-9._%/-]+)\)")
RECORD_ID = re.compile(r"(?:kv:)?(\d{4}\.\d{5})(?:v\d+)?")
MAX_FILE = 60 * 1024 * 1024
MAX_REPORTS = 50
REPRODUCTION = "Independent reproduction"
COURSE = "Course assignment"
CHOICES = {
    "purpose": ("Official implementation", REPRODUCTION, "Other"),
    "type": ("Research paper", "Thesis", COURSE, "Benchmark or leaderboard entry", "Artifact evaluation", "Other"),
    "field": (
        "Machine learning", "Natural language processing", "Computer vision", "Robotics", "Systems and performance",
        "Databases", "Networking", "Security and cryptography", "Programming languages and compilers",
        "Algorithms and theory", "Human-computer interaction", "Scientific computing", "Bioinformatics and health",
        "Physics and astronomy", "Chemistry and materials", "Earth and climate", "Engineering",
        "Mathematics and statistics", "Economics and social science", "Other",
    ),
}
LINKS = ("paper", "code", "original_code")
# Form label -> metadata key, for everything a correction may change without a new version.
DESCRIPTIVE = (
    ("Title", "title"), ("Abstract", "abstract"), ("Code licence", "license"), ("Purpose", "purpose"),
    ("Type of work", "type"), ("Field", "field"), ("Paper link", "paper"), ("Code repository", "code"),
    ("Original code", "original_code"),
)


class Rejected(Exception):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def form(body: str) -> dict[str, str]:
    """Issue forms render as '### Label' sections; empty optional fields read '_No response_'."""
    fields: dict[str, str] = {}
    for part in re.split(r"^### ", body or "", flags=re.M)[1:]:
        label, _, value = part.partition("\n")
        value = value.strip()
        fields[label.strip()] = "" if value == "_No response_" else value
    return fields


def attachments(text: str) -> list[tuple[str, str]]:
    return [(Path(name).name, url) for name, url in ATTACHMENT.findall(text or "")]


def download(url: str, dest: Path, magic: bytes) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "kveritas-records"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        raise Rejected(f"{dest.name}: larger than {MAX_FILE >> 20} MB")
    if not data.startswith(magic):
        raise Rejected(f"{dest.name}: not a {'PDF' if magic == b'%PDF' else 'zip'} file")
    dest.write_bytes(data)


def seal_of(pdf: Path) -> dict:
    data = pdf.read_bytes()
    i, j = data.find(BEGIN), data.find(END)
    if i < 0 or j < i:
        raise Rejected(f"{pdf.name}: not a sealed K-Veritas report")
    return json.loads(data[i + len(BEGIN):j])


def kv_verify(pdf: Path, name: str) -> None:
    out = subprocess.run([str(KV), "verify", str(pdf)], capture_output=True, text=True, timeout=900).stdout
    lines = out.splitlines()
    if not lines or lines[0].strip() != "VERIFIED":
        raise Rejected(f"{name}: does not verify ({lines[0] if lines else 'no output'})")
    if "Cryptographic status: AUTHENTIC" not in out:
        raise Rejected(f"{name}: the server audit did not confirm the report")
    anchors = next((l for l in lines if "Run anchors:" in l), "")
    if not re.search(r"all \d+ run\(s\) anchored", anchors):
        raise Rejected(f"{name}: run anchors not intact ({anchors.strip() or 'none'})")


def versions(base: str) -> list[Path]:
    return sorted(ROOT.glob(f"records/*/{base}/v*"), key=lambda p: int(p.name[1:]))


def all_metadata() -> list[dict]:
    return [yaml.safe_load(p.read_text()) for p in ROOT.glob("records/*/*/v*/metadata.yaml")]


def base_of(record_id: str) -> str:
    return record_id.removeprefix("kv:").split("v")[0]


def authors_of(text: str) -> list[dict]:
    out = []
    for line in (text or "").splitlines():
        line = line.strip().lstrip("-*").strip()
        if not line:
            continue
        m = re.fullmatch(r"(.+?)\s*\((.+)\)", line)
        out.append({"name": m.group(1), "affiliation": m.group(2)} if m else {"name": line})
    return out


def lines_of(text: str) -> list[str]:
    return [l.strip().lstrip("-*").strip() for l in (text or "").splitlines() if l.strip()]


def checked_terms(fields: dict) -> bool:
    terms = fields.get("Terms", "")
    return "- [ ]" not in terms and terms.count("- [X]") + terms.count("- [x]") >= 3


def apply_fields(fields: dict, meta: dict) -> list[str]:
    """Copy descriptive form fields into meta; returns the keys that changed."""
    changed = []
    for label, key in DESCRIPTIVE:
        value = fields.get(label, "").strip()
        if not value:
            continue
        if key in CHOICES and value not in CHOICES[key]:
            raise Rejected(f"'{label}' must be one of: {', '.join(CHOICES[key])}")
        if key in LINKS and not re.fullmatch(r"https://\S{4,300}", value):
            raise Rejected(f"'{label}' must be an https:// link")
        if meta.get(key) != value:
            meta[key] = value
            changed.append(key)
    if fields.get("Authors"):
        authors = authors_of(fields["Authors"])
        if authors != meta.get("authors"):
            meta["authors"] = authors
            changed.append("authors")
    if fields.get("Tags"):
        tags = [t.strip() for t in fields["Tags"].split(",") if t.strip()]
        if tags != meta.get("tags"):
            meta["tags"] = tags
            changed.append("tags")
    return changed


def validate(meta: dict, fields: dict) -> None:
    if not meta.get("authors"):
        raise Rejected("at least one author is required")
    if meta.get("purpose") == REPRODUCTION and not meta.get("original_code"):
        raise Rejected("an independent reproduction needs the 'Original code' link")
    if meta.get("type") == COURSE and "[X]" not in fields.get("Course assignment", "").upper():
        raise Rejected("a course assignment needs the instructor's permission to publish (tick the box)")


def build(work: Path) -> dict:
    """Turn the issue into a submission: a new record, a new version, or a correction."""
    fields = form(os.environ.get("ISSUE_BODY", ""))
    labels = json.loads(os.environ.get("ISSUE_LABELS", "[]"))
    author = os.environ.get("ISSUE_AUTHOR", "")
    maintainer = os.environ.get("AUTHOR_PERMISSION", "") in ("admin", "maintain")
    if not checked_terms(fields):
        raise Rejected("all three terms must be accepted")

    (work / "reports").mkdir(parents=True)
    (work / "bundles").mkdir()
    reports: list[dict] = []
    supersedes = ""

    if "update" in labels:
        m = RECORD_ID.fullmatch(fields.get("Record ID", "").strip())
        if not m or not versions(m.group(1)):
            raise Rejected("Record ID does not name a published record")
        supersedes = m.group(1)
        latest = versions(supersedes)[-1]
        prev = yaml.safe_load((latest / "metadata.yaml").read_text())
        owner = (prev.get("submitted_by") or {}).get("github")
        if not maintainer and (not owner or owner != author):
            raise Rejected(f"only the original submitter{f' (@{owner})' if owner else ''} or a maintainer can update this record")
        note = fields.get("What changed", "").strip()
        if not note:
            raise Rejected("describe what changed")
        remove = {r.strip() for r in re.split(r"[,\s]+", fields.get("Remove reports", "")) if r.strip()}
        unknown = remove - {r["id"] for r in prev["reports"]}
        if unknown:
            raise Rejected(f"cannot remove unknown reports: {', '.join(sorted(unknown))}")
        new_reports = fields.get("Add reports", "")
        meta = {k: v for k, v in prev.items() if k not in ("id", "version", "published", "status", "issue", "results", "reports", "files", "changes", "corrections")}
        changed = apply_fields(fields, meta)
        validate(meta, fields)

        if not remove and not attachments(new_reports):
            if not changed:
                raise Rejected("nothing changed")
            return {"kind": "correction", "vdir": latest, "id": prev["id"], "changed": changed, "note": note, "by": author, "meta": meta}

        prev_full = prev["id"].removeprefix("kv:")
        for r in prev["reports"]:
            if r["id"] in remove:
                continue
            shutil.copyfile(ROOT / r["files"]["report"]["path"], work / "reports" / f"{r['id']}.pdf")
            if r["files"].get("bundle"):
                subprocess.run(["gh", "release", "download", prev_full, "-R", REPO, "-p", f"{r['id']}.kvbundle.zip",
                                "-D", str(work / "bundles"), "--clobber"], check=True, timeout=300)
            reports.append({"id": r["id"], "label": r["label"]})
        meta["changes"] = {"text": note, "by": author}
        new_labels, new_bundles = fields.get("Labels for added reports", ""), fields.get("Code bundles", "")
        next_n = max((int(r["id"][1:]) for r in prev["reports"]), default=0) + 1
    else:
        for field in ("Title", "Authors", "Your name", "Abstract", "Code licence", "Purpose", "Type of work", "Field",
                      "Sealed reports", "Report labels"):
            if not fields.get(field):
                raise Rejected(f"'{field}' is required")
        submitter = {"name": fields["Your name"], "github": author}
        if fields.get("Your affiliation"):
            submitter["affiliation"] = fields["Your affiliation"]
        meta = {"submitted_by": submitter}
        apply_fields(fields, meta)
        validate(meta, fields)
        new_reports, new_labels, new_bundles = fields["Sealed reports"], fields["Report labels"], fields.get("Code bundles", "")
        next_n = 1

    pdfs, names = attachments(new_reports), lines_of(new_labels)
    if any(not n.lower().endswith(".pdf") for n, _ in pdfs):
        raise Rejected("only report PDFs go in the reports field")
    if len(names) != len(pdfs):
        raise Rejected(f"{len(pdfs)} report(s) attached but {len(names)} label(s) given; one label per report, same order")
    for k, ((name, url), label) in enumerate(zip(pdfs, names)):
        rid = f"r{next_n + k}"
        download(url, work / "reports" / f"{rid}.pdf", b"%PDF")
        reports.append({"id": rid, "label": label})
    for k, (name, url) in enumerate(attachments(new_bundles)):
        if not name.lower().endswith(".zip"):
            raise Rejected(f"{name}: bundles must be .kvbundle.zip files")
        download(url, work / "bundles" / f"new{k}.kvbundle.zip", b"PK")
    if not reports:
        raise Rejected("a record needs at least one report")
    if len(reports) > MAX_REPORTS:
        raise Rejected(f"at most {MAX_REPORTS} reports per record")
    meta["reports"] = reports
    return {"kind": "version" if supersedes else "new", "meta": meta, "supersedes": supersedes}


def check(work: Path, sub: dict) -> list[dict]:
    if sub["kind"] == "correction":
        return []
    meta, supersedes = sub["meta"], sub["supersedes"]
    bundles = {p: sha256(p) for p in (work / "bundles").glob("*.kvbundle.zip")}
    used: set[Path] = set()
    facts = []
    for r in meta["reports"]:
        pdf = work / "reports" / f"{r['id']}.pdf"
        seal = seal_of(pdf)
        if not seal.get("session") or not seal.get("seal"):
            raise Rejected(f"{r['id']}: not a sealed report")
        kv_verify(pdf, r["id"])
        signed_bundle = seal["seal"].get("checkout_bundle_hash", "")
        if not signed_bundle:
            raise Rejected(f"{r['id']}: sealed without open disclosure, so it has no code bundle (use kveritas init --disclosure open)")
        matches = [p for p, h in bundles.items() if h == signed_bundle]
        if len(matches) != 1:
            raise Rejected(f"{r['id']}: needs exactly one attached bundle matching the hash it signed")
        used.add(matches[0])
        facts.append({"meta": r, "pdf": pdf, "seal": seal, "bundle": matches[0]})
    extra = set(bundles) - used
    if extra:
        raise Rejected(f"{len(extra)} bundle(s) match no report")

    hashes = [f["seal"]["seal"]["data_hash"] for f in facts]
    if len(set(hashes)) != len(hashes):
        raise Rejected("the same report appears twice")
    taken = {}
    for md in all_metadata():
        for r in md.get("reports", []):
            taken[r["data_hash"]] = base_of(md["id"])
    for f, h in zip(facts, hashes):
        if h in taken and taken[h] != supersedes:
            raise Rejected(f"{f['meta']['id']}: already published in kv:{taken[h]}")
    return facts


def next_id(supersedes: str) -> tuple[str, int]:
    if supersedes:
        return supersedes, int(versions(supersedes)[-1].name[1:]) + 1
    month = dt.datetime.now(dt.timezone.utc).strftime("%y%m")
    seqs = [int(p.name.split(".")[1]) for p in (ROOT / "records" / month).glob(f"{month}.*")]
    return f"{month}.{max(seqs, default=0) + 1:05d}", 1


def results_of(seal: dict) -> list[dict]:
    out = [{"metric": c["metric"], "value": c["value"]} for run in seal.get("runs") or [] for c in run.get("claims") or []]
    if not out and seal.get("runs"):
        last = {m["name"]: m["value"] for m in seal["runs"][-1].get("metrics") or []}
        out = [{"metric": k, "value": v} for k, v in last.items()]
    return out


def write_cover(vdir: Path) -> None:
    meta = vdir / "metadata.yaml"
    subprocess.run([str(KV), "archive-record", str(meta)], check=True, timeout=600)
    md = yaml.safe_load(meta.read_text())
    rec = vdir / "record.pdf"
    md["files"] = {"record": {"path": str(rec.relative_to(ROOT)), "sha256": sha256(rec), "size": rec.stat().st_size}}
    meta.write_text(yaml.safe_dump(md, sort_keys=False, allow_unicode=True, width=100))


def publish(sub: dict, facts: list[dict]) -> str:
    meta = sub["meta"]
    base, version = next_id(sub["supersedes"])
    full = f"{base}v{version}"
    vdir = ROOT / "records" / base[:4] / base / f"v{version}"
    (vdir / "reports").mkdir(parents=True)
    assets = OUT / "assets"
    assets.mkdir(parents=True, exist_ok=True)

    reports, results = [], []
    for f in facts:
        rid = f["meta"]["id"]
        dest = vdir / "reports" / f"{rid}.pdf"
        shutil.copyfile(f["pdf"], dest)
        asset = assets / f"{rid}.kvbundle.zip"
        shutil.copyfile(f["bundle"], asset)
        seal = f["seal"]
        reports.append({
            "id": rid,
            "label": f["meta"]["label"],
            "session": seal["session"]["id"],
            "data_hash": seal["seal"]["data_hash"],
            "sealed_at": seal["seal"]["sealed_at"],
            "signer": "K-Veritas server",
            "files": {
                "report": {"path": str(dest.relative_to(ROOT)), "sha256": sha256(dest), "size": dest.stat().st_size},
                "bundle": {"path": f"bundles/{full}/{rid}.kvbundle.zip", "sha256": sha256(asset), "size": asset.stat().st_size},
            },
        })
        results += results_of(seal)

    today = dt.datetime.now(dt.timezone.utc)
    published = {
        "id": f"kv:{full}",
        "version": version,
        "title": meta["title"],
        "authors": meta["authors"],
        "submitted_by": meta["submitted_by"],
        "abstract": " ".join(str(meta["abstract"]).split()),
        "tags": meta.get("tags") or [],
        "license": meta["license"],
        **{k: meta[k] for k in ("purpose", "type", "field", "paper", "code", "original_code") if meta.get(k)},
        "published": today.strftime("%Y-%m-%d"),
        "status": "published",
        "issue": int(os.environ.get("ISSUE_NUMBER", "0")) or None,
    }
    if meta.get("changes"):
        published["changes"] = meta["changes"]
    published["results"] = results
    published["reports"] = reports
    (vdir / "metadata.yaml").write_text(yaml.safe_dump(published, sort_keys=False, allow_unicode=True, width=100))
    write_cover(vdir)

    index = ROOT / "index" / f"{today.strftime('%y%m')}.json"
    entries = json.loads(index.read_text()) if index.exists() else []
    entries.append({
        "id": f"kv:{full}",
        "title": meta["title"],
        "authors": [a["name"] for a in meta["authors"]],
        "submitted_by": meta["submitted_by"]["name"],
        "published": published["published"],
        "reports": len(reports),
        **{k: meta[k] for k in ("purpose", "type", "field") if meta.get(k)},
        "path": str(vdir.relative_to(ROOT)),
    })
    index.parent.mkdir(exist_ok=True)
    index.write_text(json.dumps(entries, indent=2) + "\n")
    (OUT / "published.txt").write_text(full)
    (OUT / "done.txt").write_text(f"publish {full}")
    return full


def correct(sub: dict) -> str:
    """Change descriptive fields of the latest version in place; reports and their hashes stay as they are."""
    vdir, meta = sub["vdir"], sub["meta"]
    md = yaml.safe_load((vdir / "metadata.yaml").read_text())
    for key in sub["changed"]:
        md[key] = meta[key]
    md.setdefault("corrections", []).append({
        "date": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d"),
        "fields": sub["changed"],
        "text": sub["note"],
        "by": sub["by"],
        "issue": int(os.environ.get("ISSUE_NUMBER", "0")) or None,
    })
    (vdir / "metadata.yaml").write_text(yaml.safe_dump(md, sort_keys=False, allow_unicode=True, width=100))
    write_cover(vdir)
    for index in ROOT.glob("index/*.json"):
        entries = json.loads(index.read_text())
        for e in entries:
            if e["id"] == md["id"]:
                e["title"] = md["title"]
                e["authors"] = [a["name"] for a in md["authors"]]
                for k in ("purpose", "type", "field"):
                    if md.get(k):
                        e[k] = md[k]
        index.write_text(json.dumps(entries, indent=2) + "\n")
    full = md["id"].removeprefix("kv:")
    (OUT / "done.txt").write_text(f"correct {full}")
    return full


def summary(sub: dict, facts: list[dict]) -> str:
    if sub["kind"] == "correction":
        return (f"**Automatic checks passed.** Correction of {sub['id']}: {', '.join(sub['changed'])}.\n\n"
                "The reports are unchanged, so this corrects the current version in place; no new version. "
                "A maintainer reviews it next; the `approved` label applies it.")
    rows = "\n".join(
        f"| {f['meta']['id']} | {f['meta']['label']} | {len(f['seal'].get('runs') or [])} | "
        f"`{f['seal']['seal']['data_hash'][:16]}` |" for f in facts)
    kind = f"New version of kv:{sub['supersedes']}" if sub["supersedes"] else "New record"
    return (f"**Automatic checks passed.** {kind}, {len(facts)} report(s).\n\n"
            f"| Report | Label | Runs | Data hash |\n|---|---|---|---|\n{rows}\n\n"
            "Each report verifies as server-signed with every run anchor intact, and each bundle matches "
            "the hash its report signed. A maintainer reviews the submission next; publication follows "
            "the `approved` label.")


def main() -> None:
    cmd = sys.argv[1]
    OUT.mkdir(parents=True, exist_ok=True)
    if cmd == "cover":
        m = re.fullmatch(r"(?:kv:)?(\d{4}\.\d{5})v(\d+)", sys.argv[2].strip())
        if not m:
            sys.exit(f"not a versioned record id: {sys.argv[2]}")
        write_cover(next(ROOT.glob(f"records/*/{m.group(1)}/v{m.group(2)}")))
        return

    work = OUT / "submission"
    shutil.rmtree(work, ignore_errors=True)
    try:
        sub = build(work)
        facts = check(work, sub)
    except Rejected as e:
        (OUT / "comment.md").write_text(
            f"**Rejected: automatic checks failed.**\n\n{e}\n\nFix the problem and open a new issue. "
            "See the [rules](https://github.com/KVERITAS-SCIENCE/records#rules).")
        sys.exit(1)
    if cmd == "check":
        (OUT / "comment.md").write_text(summary(sub, facts))
        return
    if sub["kind"] == "correction":
        full = correct(sub)
        (OUT / "comment.md").write_text(
            f"**Corrected [kv:{full}](https://kveritas.org/records/{full}) in place:** {', '.join(sub['changed'])}. "
            "The correction is logged on the record.")
        return
    full = publish(sub, facts)
    (OUT / "comment.md").write_text(
        f"**Published as [kv:{full}](https://kveritas.org/records/{full}).**\n\n"
        f"Cite it with the BibTeX on that page. Updates: open an *Update a record* issue with `{full.split('v')[0]}`.")


if __name__ == "__main__":
    main()
