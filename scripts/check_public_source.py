#!/usr/bin/env python3
"""Public-source integrity gate for the Research source tree (runs in both Research repositories).

1. SOURCE_MANIFEST.sha256, when present (the public repository), lists exactly the tracked files with
   their SHA-256.
2. The vendored figure renderer matches RENDERER_LOCK.json (scripts/sync_figure_renderer.py --check).
3. No tracked file contains private keys, cloud or GitHub tokens, a populated secret assignment, or a
   tracked environment/secret file. Vendored third-party libraries are covered by their lock hashes
   instead of the text scan.

    python3 scripts/check_public_source.py            # exit 1 on any finding
    python3 scripts/check_public_source.py --write-manifest
"""
import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "SOURCE_MANIFEST.sha256"
VENDOR = "research-web/assets/vendor/"
SECRET_FILES = re.compile(r"(^|/)(\.env(\..+)?|.*\.pem|.*\.key|id_rsa.*|id_ed25519.*|secrets/.*)$")
ALLOWED_SECRET_FILES = {"infrastructure/dev.env.example"}
# Reviewed exceptions: synthetic strings that exist to test a scanner (private repository only).
ALLOWED_FINDINGS = {("tests/test_public_readiness.py", "private key")}
PATTERNS = [
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"), "private key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), "GitHub token"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "Slack token"),
    (re.compile(r"(?i)\b(?:password|passwd|secret|api_?key|token)\s*[:=]\s*['\"][^'\"\s$]{12,}['\"]"), "hard-coded secret"),
]


def tracked() -> list[str]:
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], check=True, capture_output=True).stdout
    return sorted(p for p in out.decode().split("\0") if p)


def manifest_lines(files) -> str:
    return "".join(f"{hashlib.sha256((ROOT / p).read_bytes()).hexdigest()}  {p}\n" for p in files if p != MANIFEST.name)


def manifest_errors(files) -> list[str]:
    if not MANIFEST.exists():
        return []
    listed = {}
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        digest, _, path = line.partition("  ")
        listed[path] = digest
    errors = []
    actual = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in files if p != MANIFEST.name}
    for p in sorted(set(actual) | set(listed)):
        if p not in listed:
            errors.append(f"manifest: {p} is tracked but not listed")
        elif p not in actual:
            errors.append(f"manifest: {p} is listed but not tracked")
        elif listed[p] != actual[p]:
            errors.append(f"manifest: {p} hash differs")
    return errors


def content_errors(files) -> list[str]:
    errors = []
    for p in files:
        if SECRET_FILES.search(p) and p not in ALLOWED_SECRET_FILES:
            errors.append(f"{p}: secret or environment file is tracked")
        if p.startswith(VENDOR):
            continue
        try:
            text = (ROOT / p).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern, what in PATTERNS:
            if pattern.search(text) and (p, what) not in ALLOWED_FINDINGS:
                errors.append(f"{p}: {what}")
    return errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-manifest", action="store_true")
    args = ap.parse_args()
    files = tracked()
    if args.write_manifest:
        MANIFEST.write_text(manifest_lines(files), encoding="utf-8")
        files = tracked()
    errors = manifest_errors(files) + content_errors(files)
    lock = subprocess.run([sys.executable, str(ROOT / "scripts" / "sync_figure_renderer.py"), "--check"], capture_output=True, text=True)
    if lock.returncode:
        errors.append("renderer lock: " + lock.stdout.strip().replace("\n", "; "))
    for e in errors:
        print("FAIL", e)
    print(f"public source check: {len(files)} tracked files, {len(errors)} finding(s)")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
