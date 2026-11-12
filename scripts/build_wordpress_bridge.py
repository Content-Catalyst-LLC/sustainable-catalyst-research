#!/usr/bin/env python3
"""RH-PROD-06: build a deterministic installable WordPress Research bridge ZIP.

Copies the tested public-only WordPress adapter; does not deploy or enable it.
All sources stay in this repository. No credentials or private project data.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = "sc-research-bridge"
SOURCES = {
    f"{PLUGIN}/sc-research-bridge.php": ROOT / "wordpress" / PLUGIN / "sc-research-bridge.php",
    f"{PLUGIN}/readme.txt": ROOT / "wordpress" / PLUGIN / "readme.txt",
    f"{PLUGIN}/assets/research-workbench-bridge.js": ROOT / "research-web" / "assets" / "research-workbench-bridge.js",
    f"{PLUGIN}/assets/research-workbench-bridge.css": ROOT / "research-web" / "assets" / "research-workbench-bridge.css",
    f"{PLUGIN}/assets/research-navigation.js": ROOT / "research-web" / "assets" / "research-navigation.js",
    # SC-FIG-03B: the shared figure loader and the Research Hub panel. The renderer itself is the Lab plugin's.
    f"{PLUGIN}/assets/research-figures.js": ROOT / "research-web" / "assets" / "research-figures.js",
    f"{PLUGIN}/assets/research-hub-figures.js": ROOT / "research-web" / "assets" / "research-hub-figures.js",
}


def build(output: Path) -> dict:
    if output.suffix != ".zip" or not output.is_absolute():
        raise ValueError("An absolute .zip output path is required")
    assets = {}
    for target, source in SOURCES.items():
        if not source.is_file() or source.is_symlink():
            raise RuntimeError("Missing or unsafe WordPress bridge source")
        data = source.read_bytes()
        if not data or len(data) > 200_000:
            raise RuntimeError("WordPress bridge source missing or unexpectedly large")
        assets[target] = data

    # Prevent a stale/test adapter or a review-only disabled copy from shipping.
    adapter = assets[f"{PLUGIN}/assets/research-workbench-bridge.js"].decode("utf-8")
    php = assets[f"{PLUGIN}/sc-research-bridge.php"].decode("utf-8")
    required = (
        '"/public/status"', '"/articles"', '"/objects?limit=50"',
        '"/graph/metadata?limit=50"', '"/status", "/articles", "/objects", "/graph"',
        'sc-research-public-backend-status/1.0',
        "SustainableCatalystResearchConfig",
    )
    # PHP uses a separate conservative allowlist.
    for marker in required:
        if marker not in adapter:
            raise RuntimeError("Required RH-PROD-06 public adapter gate is missing")
    navigation = assets[f"{PLUGIN}/assets/research-navigation.js"].decode("utf-8")
    for marker in ("[data-tab]", "[data-tab-target]", "scrh-view-", "scrh-discover-form", "scrh-graph-svg-wrap"):
        if marker not in navigation:
            raise RuntimeError("Required public Research navigation handler is missing")
    for marker in ("sc_research_bridge_fetch", "SC_RESEARCH_BRIDGE_ENABLED",
                   "SC_RESEARCH_API_ORIGIN", "wp_safe_remote_get",
                   "sc_research_bridge_project", "sc_research_bridge_register_routes",
                   "SC_RESEARCH_FIGURE_CHANNELS = array('staging-synthetic')",
                   "SC_RESEARCH_FIGURES_LAB_RELEASE", "sc_research_figures_project",
                   "array('local', 'development', 'staging')"):
        if marker not in php:
            raise RuntimeError("Required WordPress backend gate is missing")
    figures = assets[f"{PLUGIN}/assets/research-figures.js"].decode("utf-8")
    hub = assets[f"{PLUGIN}/assets/research-hub-figures.js"].decode("utf-8")
    lab_release = re.search(r'"labRelease": "([0-9.]+)"', figures)
    if not lab_release or f"SC_RESEARCH_FIGURES_LAB_RELEASE = '{lab_release.group(1)}'" not in php:
        raise RuntimeError("Bridge Lab release does not match the figure renderer lock")
    for marker in ("BEGIN RENDERER LOCK", "configure", "renderItems", "WP_ENDPOINT"):
        if marker not in figures:
            raise RuntimeError("Required figure loader gate is missing")
    for marker in ('"staging-synthetic"', "/figures/status", "renderer.configure", "sc-research-figures-rendered"):
        if marker not in hub:
            raise RuntimeError("Required Research Hub figure gate is missing")

    manifest = {
        "schema": "sc-research-wordpress-bridge-package/1.0",
        "release": "RH-PROD-06",
        "status": "installable-disabled-by-default",
        "files_sha256": {
            target.removeprefix(PLUGIN + "/"): hashlib.sha256(data).hexdigest()
            for target, data in sorted(assets.items())
        },
    }
    assets[f"{PLUGIN}/integrity.json"] = (
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")

    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for target, data in sorted(assets.items()):
            info = ZipInfo(target, date_time=(2026, 11, 7, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data, compress_type=ZIP_DEFLATED, compresslevel=9)
    return {
        "filename": output.name,
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "files": len(assets),
        "status": "INSTALLABLE_NOT_DEPLOYED",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.output.resolve()), indent=2))


if __name__ == "__main__":
    main()
