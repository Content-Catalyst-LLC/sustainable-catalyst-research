<?php
/* RH-PROD-06 WordPress adapter contract, executed by CI with PHP CLI.
 * Stubs are deliberately narrow and never use a live WordPress or Research host.
 */
error_reporting(E_ALL);
define('ABSPATH', __DIR__ . '/');
$GLOBALS['sc_test_options'] = array();
$GLOBALS['sc_test_requests'] = array();
$GLOBALS['sc_test_response'] = array();

class WP_Error {
    public string $code;
    public function __construct(string $code, string $message = '', array $data = array()) { $this->code = $code; }
}
function is_wp_error($value): bool { return $value instanceof WP_Error; }
function add_action($hook, $callback): void {}
function get_option($name, $fallback = '') { return $GLOBALS['sc_test_options'][$name] ?? $fallback; }
function update_option($key, $value, $autoload = false) { $GLOBALS['sc_test_options'][$key] = $value; }
function home_url($path = '') { return 'https://sustainablecatalyst.com/' . ltrim($path, '/'); }
function wp_parse_url($url, $component = -1) { return parse_url($url, $component); }
function wp_safe_remote_get($url, $args) {
    $GLOBALS['sc_test_requests'][] = array('url' => $url, 'args' => $args);
    return $GLOBALS['sc_test_response'];
}
function wp_remote_retrieve_response_code($result) { return $result['code'] ?? 0; }
function wp_remote_retrieve_header($result, $name) { return $result['headers'][$name] ?? ''; }
function wp_remote_retrieve_body($result) { return $result['body'] ?? ''; }

require dirname(__DIR__, 2) . '/wordpress/sc-research-bridge/sc-research-bridge.php';

function sc_assert($condition, string $message): void {
    if (!$condition) { fwrite(STDERR, "FAIL: " . $message . "\n"); exit(1); }
}
function fake_json_response(array $payload, int $status = 200): void {
    $GLOBALS['sc_test_response'] = array(
        'code' => $status,
        'headers' => array('content-type' => 'application/json; charset=utf-8'),
        'body' => json_encode($payload),
    );
}
$valid = 'https://research-api.sustainablecatalyst.com';
$invalid = array(
    'http://research-api.sustainablecatalyst.com',
    'https://sustainablecatalyst.com',
    'https://research-api.sustainablecatalyst.com.evil.test',
    'https://127.0.0.1',
    'https://user:pass@research-api.sustainablecatalyst.com',
    'https://research-api.sustainablecatalyst.com:8443',
    'https://research-api.sustainablecatalyst.com/api/research',
    'https://research-api.sustainablecatalyst.com?redirect=evil',
    'https://research-api.sustainablecatalyst.com#fragment',
    'file:///etc/passwd',
);
sc_assert(sc_research_bridge_normalize_origin($valid . '/') === $valid, 'valid HTTPS origin accepted');
foreach ($invalid as $url) {
    sc_assert(sc_research_bridge_normalize_origin($url) === '', 'invalid origin rejected: ' . $url);
}
sc_assert(!sc_research_bridge_enabled(), 'disabled by default');
sc_assert(is_wp_error(sc_research_bridge_fetch('status')), 'disabled status must fail closed');
sc_assert(count($GLOBALS['sc_test_requests']) === 0, 'disabled must not contact upstream');

$GLOBALS['sc_test_options']['sc_research_api_origin'] = $valid;
$GLOBALS['sc_test_options']['sc_research_bridge_enabled'] = '1';
sc_assert(sc_research_bridge_enabled(), 'admin-enabled state read from WP options');
sc_assert(sc_research_bridge_origin() === $valid, 'validated origin from admin options');
sc_assert(is_wp_error(sc_research_bridge_fetch('private/projects')), 'arbitrary route rejected');
sc_assert(count($GLOBALS['sc_test_requests']) === 0, 'arbitrary route must not request upstream');

$publicStatus = array(
    'schema' => 'sc-research-public-backend-status/1.0',
    'source' => 'research-postgresql', 'storage' => 'ready',
    'approved_areas' => 3, 'approved_articles' => 2, 'owner_scope' => 'NEVER_PUBLIC',
);
fake_json_response($publicStatus);
$view = sc_research_bridge_fetch('status');
sc_assert(!is_wp_error($view), 'verified backend status accepted');
sc_assert(!isset($view['owner_scope']), 'status excludes unknown private fields');
sc_assert($view['approved_articles'] === 2, 'approved counts preserved');
sc_assert(count($GLOBALS['sc_test_requests']) === 1, 'approved status request performed exactly once');
$request = $GLOBALS['sc_test_requests'][0];
sc_assert($request['url'] === $valid . '/api/research/v1/public/status', 'fixed backend path only');
sc_assert($request['args']['cookies'] === array(), 'no browser cookie forwarding');
sc_assert($request['args']['redirection'] === 0 && $request['args']['sslverify'] === true, 'no redirects or SSL bypass');
sc_assert($request['args']['timeout'] <= 6, 'bounded timeout');

fake_json_response(array_merge($publicStatus, array('storage' => 'not-ready')));
sc_assert(is_wp_error(sc_research_bridge_fetch('status')), 'misleading upstream status rejected');

$public = array(
    'id' => 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
    'title' => 'Public research dataset', 'summary' => 'Cleared summary',
    'kind' => 'dataset', 'authority' => 'site-intelligence',
    'review_status' => 'approved-public', 'rights' => 'cleared',
    'source_id' => 'PRIVATE_FILE_PATH', 'owner_scope' => 'PRIVATE_OWNER_ID',
);
$private = array_merge($public, array('id' => 'bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee', 'rights' => 'pending'));
fake_json_response(array(
    'schema' => 'sc-research-public-objects/1.0', 'items' => array($public, $private),
));
$data = sc_research_bridge_fetch('objects');
sc_assert(count($data['items']) === 1, 'private or uncleared objects omitted');
sc_assert(!str_contains(json_encode($data), 'PRIVATE_'), 'private source/owner metadata excluded');

fake_json_response(array(
    'schema' => 'sc-research-public-object-graph/1.0',
    'nodes' => array($public, $private),
    'edges' => array(
        array('source' => $public['id'], 'target' => $private['id'], 'relation' => 'related-to'),
        array('source' => $public['id'], 'target' => $public['id'], 'relation' => 'related-to'),
    ),
));
$graph = sc_research_bridge_fetch('graph');
sc_assert(count($graph['nodes']) === 1, 'unapproved graph node omitted');
sc_assert(count($graph['edges']) === 1, 'edges to unapproved graph nodes omitted');

$GLOBALS['sc_test_options']['sc_research_bridge_enabled'] = '0';
sc_assert(is_wp_error(sc_research_bridge_fetch('articles')), 'disable toggle is enforced instantly');

echo "PASS: WordPress Research bridge origin, settings, fail-closed routing and public-only projection\n";
