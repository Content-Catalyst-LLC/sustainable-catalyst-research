"""SC-FIG-02B: the public frontend serves the Lab renderer byte-for-byte as locked."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = json.loads((ROOT / "research-web/assets/vendor/sc-figure-renderer/RENDERER_LOCK.json").read_text(encoding="utf-8"))


def test_vendored_renderer_matches_its_lock():
    out = subprocess.run([sys.executable, str(ROOT / "scripts/sync_figure_renderer.py"), "--check"], capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr


def test_lock_pins_the_lab_libraries_lab_itself_pins():
    pins = {f["role"]: f["sha256"] for f in LOCK["files"]}
    # The same SHA-256 values the Lab plugin refuses to serve without (FIG-02A / FIG-02B PHP loaders).
    assert pins["plotly"] == "e2b0f77ee8156ff9d525852fe4acb7bfe7d85e8a2e1da0523f6d16209b848968"
    assert pins["vega"] == "8f6a3587cf8d4f42c7e08120e3eb05d067e746d554e39d2dcf52acc0bd5ba28f"
    assert pins["vega-lite"] == "83e215e84e308ee8688fa6503ba0f1cf8a3c05c0d61df2841a0b92bf86809e00"
    assert pins["vega-interpreter"] == "3bba66a9912c02968723f46d4fbf340e32ea752b6ccfdc1a321458ce9ea195d7"
    assert all(f["integrity"].startswith("sha384-") for f in LOCK["files"])


def test_frontend_has_no_remote_or_eval_paths():
    page = (ROOT / "research-web/index.html").read_text(encoding="utf-8")
    loader = (ROOT / "research-web/assets/research-figures.js").read_text(encoding="utf-8")
    nginx = (ROOT / "research-web/nginx.conf").read_text(encoding="utf-8")
    assert '<script src="/assets/research-figures.js" defer></script>' in page
    for token in ("innerHTML", "eval(", "new Function", "http://", "https://", "document.write"):
        assert token not in loader
    csp = nginx.split("Content-Security-Policy \"", 1)[1].split("\"", 1)[0]
    assert "script-src 'self';" in csp and "unsafe-eval" not in csp and "object-src 'none'" in csp
