<?php
/* SC-FIG-04.1 / 05.3: bridge 0.5.x installs only beside Lab 0.163.1.9 (PHP CLI, stubbed WordPress).
 * Each case runs in its own PHP process because the active Lab release is a constant.
 */
error_reporting(E_ALL);
$case = $argv[1] ?? '';
if ($case === '') {
    $cases = array('no-lab' => 'ok', 'lab-0.163.1.9' => 'ok', 'lab-0.163.1.8' => 'refused', 'lab-0.163.1.7' => 'refused', 'lab-0.163.1.4' => 'refused');
    foreach ($cases as $c => $want) {
        $out = shell_exec(escapeshellarg(PHP_BINARY) . ' ' . escapeshellarg(__FILE__) . ' ' . escapeshellarg($c) . ' 2>&1');
        if (trim((string) $out) !== 'PASS') { fwrite(STDERR, "FAIL ($c): $out\n"); exit(1); }
    }
    echo "PASS - SC-FIG-04.1/05.3 bridge/Lab pairing guard (" . count($cases) . " activation cases, activate_plugin and notice checks)\n";
    exit(0);
}

define('ABSPATH', __DIR__ . '/');
$plugins = sys_get_temp_dir() . '/sc-bridge-pairing-' . getmypid();
define('WP_PLUGIN_DIR', $plugins);
if (preg_match('/^lab-(.+)$/', $case, $m)) { define('SC_LAB_RELEASE_VERSION', $m[1]); }
$GLOBALS['hooks'] = array();
$GLOBALS['activation'] = array();
$GLOBALS['can'] = true;
class WPDie extends Exception {}
class WP_Error { public function __construct($c = '', $m = '', $d = array()) {} }
function is_wp_error($v): bool { return $v instanceof WP_Error; }
function add_action($hook, $callback, $prio = 10, $args = 1): void { $GLOBALS['hooks'][$hook][] = $callback; }
function register_activation_hook($file, $callback): void { $GLOBALS['activation'][] = array($file, $callback); }
function add_filter($hook, $callback, $priority = 10, $args = 1): void {}
function get_option($name, $fallback = '') { return $fallback; }
function current_user_can($cap) { return $GLOBALS['can']; }
function esc_html($s) { return htmlspecialchars((string) $s, ENT_QUOTES); }
function wp_die($msg, $title = '', $args = array()) { throw new WPDie($msg . ' [' . $args['response'] . ']'); }
function get_file_data($file, $headers) {
    $text = file_get_contents($file, false, null, 0, 8192);
    $out = array();
    foreach ($headers as $k => $label) { $out[$k] = preg_match('/^[ \t\/*#@]*' . preg_quote($label, '/') . ':(.*)$/mi', $text, $mm) ? trim($mm[1]) : ''; }
    return $out;
}
require dirname(__DIR__, 2) . '/wordpress/sc-research-bridge/sc-research-bridge.php';

function check($cond, $msg) { if (!$cond) { fwrite(STDERR, "FAIL: $msg\n"); exit(1); } }
function dies(callable $fn) { try { $fn(); return null; } catch (WPDie $e) { return $e->getMessage(); } }
function plugin($rel, $name, $version) {
    @mkdir(dirname(WP_PLUGIN_DIR . '/' . $rel), 0700, true);
    file_put_contents(WP_PLUGIN_DIR . '/' . $rel, "<?php\n/**\n * Plugin Name: $name\n * Version: $version\n */\n");
}

// Bridge activation: allowed with no Lab or Lab 0.163.1.9; refused (409) beside any other Lab release.
check(count($GLOBALS['activation']) === 1 && $GLOBALS['activation'][0][1] === 'sc_research_bridge_on_activate', 'activation hook registered');
$msg = dies('sc_research_bridge_on_activate');
if ($case === 'no-lab' || $case === 'lab-0.163.1.9') {
    check($msg === null && sc_research_bridge_lab_mismatch() === null, "$case: bridge activates");
} else {
    $lab = substr($case, 4);
    check($msg !== null && strpos($msg, "requires Sustainable Catalyst Lab 0.163.1.9; Lab $lab is not compatible") !== false && strpos($msg, '[409]') !== false, "$case: refused: $msg");
    check(sc_research_figures_lab_ready() === false, "$case: figures stay off");
    ob_start(); foreach ($GLOBALS['hooks']['admin_notices'] as $cb) { $cb(); } $out = ob_get_clean();
    check(strpos($out, 'Research figures stay off') !== false, "$case: admin notice");
    $GLOBALS['can'] = false;
    ob_start(); foreach ($GLOBALS['hooks']['admin_notices'] as $cb) { $cb(); } check(ob_get_clean() === '', "$case: no notice for other users");
}

// With the bridge active: activating another Lab release is refused; 0.163.1.9 and other plugins are not.
plugin('sustainable-catalyst-lab/sustainable-catalyst-lab.php', 'Sustainable Catalyst Lab', '0.163.1.7');
check(strpos((string) dies(function () { sc_research_bridge_on_plugin_activation('sustainable-catalyst-lab/sustainable-catalyst-lab.php'); }), 'Lab 0.163.1.7 is not compatible') !== false, 'Lab 0.163.1.7 activation refused');
plugin('lab-copy/main.php', 'Sustainable Catalyst Lab', '0.163.1.4');
check(dies(function () { sc_research_bridge_on_plugin_activation('lab-copy/main.php'); }) !== null, 'Lab in another directory recognised by its header');
plugin('sustainable-catalyst-lab/sustainable-catalyst-lab.php', 'Sustainable Catalyst Lab', '0.163.1.9');
check(dies(function () { sc_research_bridge_on_plugin_activation('sustainable-catalyst-lab/sustainable-catalyst-lab.php'); }) === null, 'Lab 0.163.1.9 activation allowed');
plugin('sustainable-catalyst-lab/sustainable-catalyst-lab.php', 'Sustainable Catalyst Lab', '0.163.1.8');
check(strpos((string) dies(function () { sc_research_bridge_on_plugin_activation('sustainable-catalyst-lab/sustainable-catalyst-lab.php'); }), 'Lab 0.163.1.8 is not compatible') !== false, 'Lab 0.163.1.8 activation refused');
plugin('other/other.php', 'Another Plugin', '0.163.1.7');
check(dies(function () { sc_research_bridge_on_plugin_activation('other/other.php'); }) === null, 'unrelated plugin allowed');
check(dies(function () { sc_research_bridge_on_plugin_activation('missing/missing.php'); }) === null, 'missing file ignored');

foreach (glob($plugins . '/*/*.php') as $f) { unlink($f); }
foreach (glob($plugins . '/*') as $d) { rmdir($d); }
rmdir($plugins);
echo "PASS";
