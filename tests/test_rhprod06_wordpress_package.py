"""RH-PROD-06: installable plugin packaging and disabled-by-default invariants."""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/build_wordpress_bridge.py"

spec = importlib.util.spec_from_file_location("rhprod06_wordpress_package", SCRIPT)
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)


def test_installer_zip_contains_real_wordpress_plugin_and_local_adapter(tmp_path):
    artifact = tmp_path / "sc-research-bridge.zip"
    result = package.build(artifact)
    assert result["status"] == "INSTALLABLE_NOT_DEPLOYED"
    assert result["files"] == 8
    assert result["sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    with ZipFile(artifact) as z:
        assert set(z.namelist()) == {
            "sc-research-bridge/sc-research-bridge.php",
            "sc-research-bridge/assets/research-workbench-bridge.js",
            "sc-research-bridge/assets/research-navigation.js",
            "sc-research-bridge/assets/research-workbench-bridge.css",
            "sc-research-bridge/assets/research-figures.js",
            "sc-research-bridge/assets/research-hub-figures.js",
            "sc-research-bridge/readme.txt",
            "sc-research-bridge/integrity.json",
        }
        manifest = json.loads(z.read("sc-research-bridge/integrity.json"))
        assert manifest["status"] == "installable-disabled-by-default"
        assert manifest["schema"] == "sc-research-wordpress-bridge-package/1.0"
        for name, digest in manifest["files_sha256"].items():
            assert digest == hashlib.sha256(z.read("sc-research-bridge/" + name)).hexdigest()
        plugin = z.read("sc-research-bridge/sc-research-bridge.php").decode()
        assert "Plugin Name: Sustainable Catalyst Research Bridge" in plugin
        assert "SC_RESEARCH_BRIDGE_ENABLED" in plugin
        assert "SC_RESEARCH_API_ORIGIN" in plugin
        assert "wp_safe_remote_get" in plugin


def test_installer_bytes_reproducible(tmp_path):
    first, second = tmp_path / "first.zip", tmp_path / "second.zip"
    package.build(first)
    package.build(second)
    assert first.read_bytes() == second.read_bytes()


def test_plugin_has_no_private_http_proxy_and_no_auto_enable():
    code = (ROOT / "wordpress/sc-research-bridge/sc-research-bridge.php").read_text()
    adapter = (ROOT / "research-web/assets/research-workbench-bridge.js").read_text()
    assert "SC_RESEARCH_BRIDGE_ENABLED === true" in code
    assert "SC_RESEARCH_API_ORIGIN" in code
    assert "wp_safe_remote_get" in code
    assert "'redirection' => 0" in code
    assert "'sslverify' => true" in code
    assert "'cookies' => array()" in code
    assert "manage_options" in code
    assert "is_page('research-hub')" in code
    assert "'methods' => 'GET'" in code
    assert "'permission_callback' => '__return_true'" in code
    assert "wp_remote_post(" not in code
    assert "check_admin_referer('sc_research_bridge_save')" in code
    assert "current_user_can('manage_options')" in code
    assert "admin_post_sc_research_bridge_save" in code
    assert "sc_research_bridge_normalize_origin" in code
    assert "wp_safe_redirect" in code
    assert "sc_research_bridge_fetch('status')" in code
    assert "Authorization" not in code
    assert "/private/projects" not in code
    assert "wp_add_inline_script" in code
    assert "SustainableCatalystResearchConfig" in code
    assert 'var API_PREFIX = "/api/research/v1";' in adapter
    assert r"wp-json\/sc-research" in adapter
    assert 'wpConfig.enabled === true' in adapter
    assert "data-sc-public-api" in adapter
    navigation = (ROOT / "research-web/assets/research-navigation.js").read_text()
    assert 'root.querySelectorAll("[data-tab]")' in navigation
    assert "scrh-discover-form" in navigation
    assert "scrh-graph-svg-wrap" in navigation
    assert "External scholarly discovery is not enabled" in navigation


def test_package_builder_refuses_bad_output_suffix(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        package.build(tmp_path / "not-a-zip.json")


def test_figure_assets_ship_with_the_lock_and_no_renderer_copy(tmp_path):
    """SC-FIG-03B: the bridge ships the loader and hub panel, never a second renderer or vendored library."""
    artifact = tmp_path / "sc-research-bridge.zip"
    package.build(artifact)
    with ZipFile(artifact) as z:
        names = z.namelist()
        assert not any("plotly" in n or "vega" in n or "vendor" in n for n in names)
        figures = z.read("sc-research-bridge/assets/research-figures.js").decode()
        plugin = z.read("sc-research-bridge/sc-research-bridge.php").decode()
    assert '"labRelease": "0.163.1.9"' in figures
    assert "SC_RESEARCH_FIGURES_LAB_RELEASE = '0.163.1.9'" in plugin
    assert "'application/vnd.sc.maplibre-view.v1+json'" in plugin and '"kind": "worker"' in figures
    assert "SC_RESEARCH_FIGURE_CHANNELS = array('staging-synthetic')" in plugin
    assert "'Cache-Control', 'no-store'" in plugin
