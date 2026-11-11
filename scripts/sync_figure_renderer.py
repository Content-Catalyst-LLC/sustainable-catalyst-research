#!/usr/bin/env python3
"""SC-FIG-02B: vendor the Lab sc.figure-bundle.v1 renderer into the Research public frontend.

Research does not have its own figure renderer. It serves byte-identical copies of the Lab Graph
Studio renderer modules and their pinned, locally packaged libraries, taken from one Lab commit.
The lock records every file's SHA-256 and the matching Subresource Integrity value; the browser
refuses any file whose bytes differ, and tests/test_figure_renderer_lock.py checks the lock.

    python3 scripts/sync_figure_renderer.py --lab <Lab checkout> --rev <Lab commit>   # copy + lock
    python3 scripts/sync_figure_renderer.py --check                                    # verify only

Files are read with `git show <rev>:<path>`, so a dirty Lab working tree is never vendored.
"""
import argparse
import base64
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "research-web" / "assets" / "vendor" / "sc-figure-renderer"
LOCK = VENDOR / "RENDERER_LOCK.json"
LOADER = ROOT / "research-web" / "assets" / "research-figures.js"
LOCK_SCHEMA = "sc-research-figure-renderer-lock/1.0"

# Load order matters: libraries first, then the FIG-02A policy, the FIG-02B Vega-Lite module, the renderer.
SCRIPTS = (
    ("plotly", "assets/lib/plotly-3.3.1/plotly.min.js"),
    ("vega", "assets/lib/vega-6.4.0/vega.min.js"),
    ("vega-lite", "assets/lib/vega-lite-6.5.0/vega-lite.min.js"),
    ("vega-interpreter", "assets/lib/vega-interpreter-2.3.2/vega-interpreter.classic.js"),
    ("fig02a-policy", "assets/js/modules/graph-studio-plotly-preview-fig02a.js"),
    ("fig02b-vega-lite", "assets/js/modules/graph-studio-vega-lite-fig02b.js"),
    ("renderer", "assets/js/modules/graph-studio-figure-bundle-renderer.js"),
)
STYLES = (
    ("fig02a-style", "assets/css/sc-lab-graph-studio-plotly-preview-fig02a.css"),
    ("renderer-style", "assets/css/sc-lab-graph-studio-figure-bundle-renderer.css"),
)
LICENSES = (
    "assets/lib/plotly-3.3.1/LICENSE.txt", "assets/lib/plotly-3.3.1/SOURCE.json",
    "assets/lib/vega-6.4.0/LICENSE", "assets/lib/vega-6.4.0/SOURCE.json",
    "assets/lib/vega-lite-6.5.0/LICENSE", "assets/lib/vega-lite-6.5.0/SOURCE.json",
    "assets/lib/vega-interpreter-2.3.2/LICENSE", "assets/lib/vega-interpreter-2.3.2/SOURCE.json",
)
BEGIN, END = "// BEGIN RENDERER LOCK (scripts/sync_figure_renderer.py)", "// END RENDERER LOCK"


def sri(data: bytes) -> str:
    return "sha384-" + base64.b64encode(hashlib.sha384(data).digest()).decode()


def target(path: str) -> str:
    # Keep the Lab layout under vendor/ so licences stay next to their libraries.
    return path.removeprefix("assets/")


def lab_version(lab: Path, rev: str) -> str:
    header = subprocess.run(["git", "-C", str(lab), "show", f"{rev}:sustainable-catalyst-lab.php"],
                            check=True, capture_output=True, text=True).stdout
    match = re.search(r"^\s*\*\s*Version:\s*(\S+)", header, re.M)
    return match.group(1) if match else "unknown"


def entries(kind_items, kind):
    out = []
    for role, path in kind_items:
        data = (VENDOR / target(path)).read_bytes()
        out.append({"role": role, "kind": kind, "path": target(path), "labPath": path,
                    "sha256": hashlib.sha256(data).hexdigest(), "integrity": sri(data), "bytes": len(data)})
    return out


def loader_block(lock: dict) -> str:
    files = [{"role": f["role"], "kind": f["kind"], "path": f["path"], "integrity": f["integrity"]} for f in lock["files"]]
    body = json.dumps({"labCommit": lock["labCommit"], "labRelease": lock["labRelease"],
                       "rendererVersion": lock["rendererVersion"], "files": files}, indent=2)
    return f"{BEGIN}\nconst RENDERER_LOCK = {body};\n{END}"


def sync(lab: Path, rev: str) -> dict:
    commit = subprocess.run(["git", "-C", str(lab), "rev-parse", "--verify", rev + "^{commit}"],
                            check=True, capture_output=True, text=True).stdout.strip()
    for path in [p for _r, p in SCRIPTS + STYLES] + list(LICENSES):
        data = subprocess.run(["git", "-C", str(lab), "show", f"{commit}:{path}"], check=True, capture_output=True).stdout
        out = VENDOR / target(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
    renderer = (VENDOR / target(SCRIPTS[-1][1])).read_text(encoding="utf-8")
    version = re.search(r"var VERSION = '([^']+)'", renderer).group(1)
    lock = {"schema": LOCK_SCHEMA, "labRepository": "Content-Catalyst-LLC/sustainable-catalyst-lab",
            "labCommit": commit, "labRelease": lab_version(lab, commit), "rendererVersion": version,
            "note": "Byte-identical copies of the Lab Graph Studio renderer; update only with this script.",
            "files": entries(SCRIPTS, "script") + entries(STYLES, "style"),
            "licenses": [target(p) for p in LICENSES]}
    LOCK.write_text(json.dumps(lock, indent=1) + "\n", encoding="utf-8")
    text = LOADER.read_text(encoding="utf-8")
    start, end = text.index(BEGIN), text.index(END) + len(END)
    LOADER.write_text(text[:start] + loader_block(lock) + text[end:], encoding="utf-8")
    return lock


def check() -> list[str]:
    errors = []
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    if lock.get("schema") != LOCK_SCHEMA or not re.fullmatch(r"[0-9a-f]{40}", lock.get("labCommit", "")):
        errors.append("lock schema or Lab commit invalid")
    roles = [(f["role"], f["labPath"]) for f in lock["files"]]
    if roles != list(SCRIPTS + STYLES):
        errors.append("lock does not list the renderer files in load order")
    for f in lock["files"]:
        data = (VENDOR / f["path"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != f["sha256"] or sri(data) != f["integrity"] or len(data) != f["bytes"]:
            errors.append(f"{f['path']}: bytes differ from the lock")
    for p in lock["licenses"]:
        if not (VENDOR / p).is_file():
            errors.append(f"{p}: licence file missing")
    text = LOADER.read_text(encoding="utf-8")
    if loader_block(lock) not in text:
        errors.append("research-figures.js lock block is out of date; rerun the sync")
    listed = {f["path"] for f in lock["files"]} | set(lock["licenses"]) | {"RENDERER_LOCK.json"}
    extra = sorted(str(p.relative_to(VENDOR)) for p in VENDOR.rglob("*") if p.is_file() and str(p.relative_to(VENDOR)) not in listed)
    if extra:
        errors.append("unlisted files in the vendor directory: " + ", ".join(extra))
    return errors


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--lab", type=Path)
    ap.add_argument("--rev")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if not args.check:
        if not args.lab or not args.rev:
            ap.error("--lab and --rev are required to sync")
        lock = sync(args.lab, args.rev)
        print(f"vendored Lab {lock['labRelease']} @ {lock['labCommit'][:12]}: {len(lock['files'])} files")
    errors = check()
    for e in errors:
        print("FAIL", e)
    if errors:
        sys.exit(1)
    print("renderer lock OK")


if __name__ == "__main__":
    main()
