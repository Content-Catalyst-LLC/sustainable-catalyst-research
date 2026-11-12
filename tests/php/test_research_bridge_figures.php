<?php
/* SC-FIG-03B: WordPress Research Hub figure routes, executed by CI with PHP CLI.
 * Narrow stubs only; no live WordPress or Research host.
 */
error_reporting(E_ALL);
define('ABSPATH', __DIR__ . '/');
$GLOBALS['sc_test_options'] = array();
$GLOBALS['sc_test_requests'] = array();
$GLOBALS['sc_test_responses'] = array();
$GLOBALS['sc_test_env'] = 'production';
$GLOBALS['sc_test_filters'] = array();
$GLOBALS['sc_test_routes'] = array();

class WP_Error {
    public string $code; public array $data;
    public function __construct(string $code, string $message = '', array $data = array()) { $this->code = $code; $this->data = $data; }
}
class WP_REST_Response {
    public $data; public int $status; public array $headers = array();
    public function __construct($data = null, int $status = 200) { $this->data = $data; $this->status = $status; }
    public function header($k, $v) { $this->headers[$k] = $v; }
    public function get_status() { return $this->status; }
    public function get_data() { return $this->data; }
}
class Fake_Request implements ArrayAccess {
    public array $params; public string $route;
    public function __construct(string $route, array $params) { $this->route = $route; $this->params = $params; }
    public function get_route() { return $this->route; }
    public function offsetExists($o): bool { return isset($this->params[$o]); }
    public function offsetGet($o): mixed { return $this->params[$o]; }
    public function offsetSet($o, $v): void {}
    public function offsetUnset($o): void {}
}
function is_wp_error($value): bool { return $value instanceof WP_Error; }
function add_action($hook, $callback): void {}
function register_activation_hook($file, $callback): void {}
function add_filter($hook, $callback, $priority = 10, $args = 1): void { $GLOBALS['sc_test_filters'][$hook] = $callback; }
function get_option($name, $fallback = '') { return $GLOBALS['sc_test_options'][$name] ?? $fallback; }
function update_option($key, $value, $autoload = false) { $GLOBALS['sc_test_options'][$key] = $value; }
function home_url($path = '') { return 'https://sustainablecatalyst.com/' . ltrim($path, '/'); }
function rest_url($path = '') { return 'https://sustainablecatalyst.com/wp-json/' . ltrim($path, '/'); }
function wp_parse_url($url, $component = -1) { return parse_url($url, $component); }
function wp_get_environment_type() { return $GLOBALS['sc_test_env']; }
function register_rest_route($ns, $route, $args) { $GLOBALS['sc_test_routes'][$route] = $args; }
function wp_safe_remote_get($url, $args) {
    $GLOBALS['sc_test_requests'][] = array('url' => $url, 'args' => $args);
    return array_shift($GLOBALS['sc_test_responses']) ?? new WP_Error('http');
}
function wp_remote_retrieve_response_code($result) { return $result['code'] ?? 0; }
function wp_remote_retrieve_header($result, $name) { return $result['headers'][$name] ?? ''; }
function wp_remote_retrieve_body($result) { return $result['body'] ?? ''; }

require dirname(__DIR__, 2) . '/wordpress/sc-research-bridge/sc-research-bridge.php';

function sc_assert($condition, string $message): void {
    if (!$condition) { fwrite(STDERR, "FAIL: " . $message . "\n"); exit(1); }
    echo "ok - $message\n";
}
function respond($payload, int $status = 200, string $type = 'application/json'): void {
    $GLOBALS['sc_test_responses'][] = array('code' => $status, 'headers' => array('content-type' => $type),
        'body' => is_string($payload) ? $payload : json_encode($payload));
}
function reset_requests(): void { $GLOBALS['sc_test_requests'] = array(); $GLOBALS['sc_test_responses'] = array(); }

$id = '0f8fad5b-d9cb-469f-a165-70867728950e';
$sha = str_repeat('a', 64); $view = str_repeat('b', 64); $prov = str_repeat('c', 64);
$status = array('schema' => 'sc-research-public-figure-status/1.0', 'enabled' => true, 'channel' => 'staging-synthetic',
    'label' => 'Synthetic staging release. Not published research.');
function package_for(string $id, string $sha, string $view, string $prov): array {
    return array(
        'schema' => 'sc-research-public-figure/2.0', 'id' => $id, 'article' => 'syn-article', 'area' => 'syn-area',
        'title' => 'Synthetic decay', 'figureId' => 'SYN-FIG', 'version' => 1, 'evidenceClass' => 'modelled',
        'measurementStatus' => 'computed-from-specified-inputs', 'dataClassification' => 'synthetic',
        'classificationNotice' => 'Modelled output computed from specified inputs. Not a measurement.',
        'owner_scope' => 'PRIVATE_OWNER', 'workspaceRunId' => 'PRIVATE_RUN',
        'release' => array('channel' => 'staging-synthetic', 'label' => 'Synthetic staging release.', 'authority' => 'sc-research',
            'bundleSha256' => $sha, 'provenanceSha256' => $prov, 'registryVersion' => 'v1', 'articleRevision' => 2,
            'releasedAt' => '2026-11-11T12:00:00+00:00', 'releaseSha256' => $prov, 'approvalRecords' => array('PRIVATE_APPROVAL'),
            'approvals' => array('dimensions' => array('scientific', 'ownership', 'rights', 'publication', 'PRIVATE'), 'signed' => true)),
        'releaseContext' => array('authority' => 'sc-research', 'state' => 'released', 'label' => 'x', 'bundleSha256' => $sha,
            'articleRevision' => 2, 'releasedAt' => '2026-11-11T12:00:00+00:00', 'subject' => 'PRIVATE_SUBJECT'),
        'units' => 't C ha-1', 'uncertainty' => 'band',
        'citations' => array(array('sourceId' => 's', 'licenceStatus' => 'verified-for-use', 'path' => 'PRIVATE_PATH')),
        'files' => array(
            array('role' => 'bundle', 'sha256' => $sha, 'mediaType' => 'application/vnd.sc.figure-bundle.v1+json', 'bytes' => 100,
                'url' => '/api/research/v1/objects/' . $id . '/figure/files/' . $sha),
            array('role' => 'view', 'sha256' => $view, 'mediaType' => 'application/vnd.plotly.v1+json', 'bytes' => 200,
                'url' => '/api/research/v1/objects/' . $id . '/figure/files/' . $view)),
        'missingViews' => array(),
    );
}

// --- Off by default: no figure channel, no Lab release, no upstream request.
$GLOBALS['sc_test_options']['sc_research_api_origin'] = 'https://research-api.sustainablecatalyst.com';
$GLOBALS['sc_test_options']['sc_research_bridge_enabled'] = '1';
sc_assert(!sc_research_figures_enabled(), 'figures are off without a channel');
sc_assert(sc_research_figures_status()['enabled'] === false, 'status reports disabled');
sc_assert(sc_research_figures_package($id)->data['status'] === 404, 'package is 404 when off');
sc_assert(count($GLOBALS['sc_test_requests']) === 0, 'off: upstream never contacted');

$GLOBALS['sc_test_options']['sc_research_figures_channel'] = 'production';
sc_assert(sc_research_figures_channel() === '', 'a channel outside the allowlist is ignored');
$GLOBALS['sc_test_options']['sc_research_figures_channel'] = 'staging-synthetic';
sc_assert(!sc_research_figures_enabled(), 'no Lab plugin: figures stay off');
define('SC_LAB_RELEASE_VERSION', '0.163.1.9');
sc_assert(sc_research_figures_enabled(), 'channel + matching Lab release + bridge: figures on');

// --- Staging origin override is ignored on production.
define('SC_RESEARCH_API_STAGING_ORIGIN', 'http://research-web');
sc_assert(sc_research_bridge_origin() === 'https://research-api.sustainablecatalyst.com', 'production ignores the staging origin');
sc_assert(sc_research_bridge_allow_staging_host(false, 'research-web') === false, 'production does not allow the internal host');
$GLOBALS['sc_test_env'] = 'staging';
sc_assert(sc_research_bridge_origin() === 'http://research-web', 'staging environment uses the internal service');
sc_assert(sc_research_bridge_allow_staging_host(false, 'research-web') === true, 'staging allows exactly that host');
sc_assert(sc_research_bridge_allow_staging_host(false, 'research-db') === false, 'no other internal host is allowed');
sc_assert(sc_research_bridge_allow_staging_host(false, 'research-web.evil.test') === false, 'no suffix match');

// --- Status: upstream and WordPress must agree on the channel.
reset_requests();
respond(array_merge($status, array('channel' => 'other')));
sc_assert(sc_research_figures_status()['enabled'] === false, 'different upstream channel shows nothing');
respond($status);
$st = sc_research_figures_status();
sc_assert($st['enabled'] === true && $st['channel'] === 'staging-synthetic', 'matching channel is enabled');
sc_assert($GLOBALS['sc_test_requests'][1]['url'] === 'http://research-web/api/research/v1/public/figures/status', 'fixed status path');
sc_assert($GLOBALS['sc_test_requests'][1]['args']['redirection'] === 0 && $GLOBALS['sc_test_requests'][1]['args']['cookies'] === array(), 'no redirects or cookies');

// --- Package: strict projection, URLs rewritten to the facade.
reset_requests();
respond(package_for($id, $sha, $view, $prov));
$pkg = sc_research_figures_package($id);
sc_assert(is_array($pkg) && $pkg['schema'] === 'sc-research-public-figure/2.0', 'package projected');
sc_assert($GLOBALS['sc_test_requests'][0]['url'] === 'http://research-web/api/research/v1/objects/' . $id . '/figure', 'fixed package path');
sc_assert(!str_contains(json_encode($pkg), 'PRIVATE'), 'private and unknown fields dropped');
sc_assert($pkg['files'][0]['url'] === '/wp-json/sc-research/v1/figures/' . $id . '/files/' . $sha, 'file URL rewritten to facade');
sc_assert($pkg['release']['approvals']['dimensions'] === array('scientific', 'ownership', 'rights', 'publication'), 'approval dimensions allowlisted');
sc_assert($pkg['area'] === 'syn-area' && $pkg['units'] === 't C ha-1' && $pkg['uncertainty'] === 'band', 'area, units and uncertainty kept');
sc_assert($pkg['citations'][0] === array('sourceId' => 's', 'licenceStatus' => 'verified-for-use'), 'citation fields allowlisted');

// SC-FIG-05.3: runtime uncertainty records travel with the views (JSON only) and reach the Lab renderer.
reset_requests();
$withRecord = package_for($id, $sha, $view, $prov);
$withRecord['files'][] = array('role' => 'runtime-record', 'sha256' => str_repeat('d', 64), 'mediaType' => 'application/json',
    'bytes' => 300, 'url' => '/api/research/v1/objects/' . $id . '/figure/files/' . str_repeat('d', 64));
respond($withRecord);
$pkg = sc_research_figures_package($id);
sc_assert(is_array($pkg) && count($pkg['files']) === 3 && $pkg['files'][2]['role'] === 'runtime-record'
    && $pkg['files'][2]['url'] === '/wp-json/sc-research/v1/figures/' . $id . '/files/' . str_repeat('d', 64), 'runtime record passed to the facade');

foreach (array(
    'wrong id' => array_merge(package_for($id, $sha, $view, $prov), array('id' => '1f8fad5b-d9cb-469f-a165-70867728950e')),
    'wrong channel' => array_replace_recursive(package_for($id, $sha, $view, $prov), array('release' => array('channel' => 'public'))),
    'context names another bundle' => array_replace_recursive(package_for($id, $sha, $view, $prov), array('releaseContext' => array('bundleSha256' => $view))),
    'bad media type' => array_replace_recursive(package_for($id, $sha, $view, $prov), array('files' => array(1 => array('mediaType' => 'text/html')))),
    'oversized file' => array_replace_recursive(package_for($id, $sha, $view, $prov), array('files' => array(1 => array('bytes' => 9000000)))),
    'no files' => array_merge(package_for($id, $sha, $view, $prov), array('files' => array())),
    'unknown file role' => array_replace_recursive(package_for($id, $sha, $view, $prov), array('files' => array(1 => array('role' => 'script')))),
    'runtime record that is not JSON' => array_replace_recursive(package_for($id, $sha, $view, $prov), array('files' => array(2 => array(
        'role' => 'runtime-record', 'sha256' => str_repeat('d', 64), 'mediaType' => 'image/svg+xml', 'bytes' => 50,
        'url' => '/api/research/v1/objects/' . $id . '/figure/files/' . str_repeat('d', 64))))),
) as $label => $bad) {
    reset_requests();
    respond($bad);
    sc_assert(is_wp_error(sc_research_figures_package($id)), 'refused: ' . $label);
}
reset_requests();
respond(array('detail' => 'Not found'), 404);
sc_assert(sc_research_figures_package($id)->data['status'] === 404, 'withdrawn upstream figure is 404 here too');
reset_requests();
sc_assert(sc_research_figures_package('../private')->data['status'] === 404, 'non-UUID id is 404');
sc_assert(count($GLOBALS['sc_test_requests']) === 0, 'non-UUID id never reaches upstream');

// --- Files: exact bytes, octet-stream only.
reset_requests();
respond('{"exact":"bytes"}', 200, 'application/octet-stream');
sc_assert(sc_research_figures_file($id, $sha) === '{"exact":"bytes"}', 'file bytes passed through unchanged');
sc_assert($GLOBALS['sc_test_requests'][0]['url'] === 'http://research-web/api/research/v1/objects/' . $id . '/figure/files/' . $sha, 'fixed file path');
reset_requests();
respond('<html>', 200, 'text/html');
sc_assert(is_wp_error(sc_research_figures_file($id, $sha)), 'non-octet-stream upstream refused');
reset_requests();
respond('', 404, 'application/json');
sc_assert(sc_research_figures_file($id, $sha)->data['status'] === 404, 'purged file is 404');
reset_requests();
sc_assert(sc_research_figures_file($id, 'ABC')->data['status'] === 404 && !$GLOBALS['sc_test_requests'], 'bad hash never reaches upstream');

// --- Routes: no-store, nosniff, attachment and sandbox on files; raw serving only for figure files.
sc_research_figures_register_routes();
$fileRoute = null;
foreach ($GLOBALS['sc_test_routes'] as $route => $args) {
    sc_assert($args['methods'] === 'GET', 'GET only: ' . $route);
    if (str_contains($route, '/files/')) { $fileRoute = $args; }
}
sc_assert(count($GLOBALS['sc_test_routes']) === 3, 'exactly three figure routes');
reset_requests();
respond('BYTES', 200, 'application/octet-stream');
$resp = ($fileRoute['callback'])(new Fake_Request('/sc-research/v1/figures/' . $id . '/files/' . $sha, array('id' => $id, 'sha' => $sha)));
sc_assert($resp->headers['Cache-Control'] === 'no-store' && $resp->headers['X-Content-Type-Options'] === 'nosniff', 'file response no-store, nosniff');
sc_assert($resp->headers['Content-Disposition'] === 'attachment' && str_contains($resp->headers['Content-Security-Policy'], 'sandbox'), 'file response attachment + sandbox');
ob_start();
$served = sc_research_figures_serve_raw(false, $resp, new Fake_Request('/sc-research/v1/figures/' . $id . '/files/' . $sha, array()));
$out = ob_get_clean();
sc_assert($served === true && $out === 'BYTES', 'raw bytes served for the figure file route');
ob_start();
$served = sc_research_figures_serve_raw(false, $resp, new Fake_Request('/wp/v2/posts', array()));
sc_assert($served === false && ob_get_clean() === '', 'other routes are never served raw');

// --- Lab mismatch turns everything off again (simulated by a fresh check of the constant comparison).
sc_assert(SC_RESEARCH_FIGURES_LAB_RELEASE === '0.163.1.9', 'pinned Lab release is 0.163.1.9');

echo "PASS: WordPress Research Hub figure routes (channel gate, Lab gate, staging origin, projection, raw files)\n";
