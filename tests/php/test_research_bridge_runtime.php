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
function register_activation_hook($file, $callback): void {}
function add_filter($hook, $callback, $priority = 10, $args = 1): void {}
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

// SC-PUB-ENDTOEND-01: one released article; an independent allowlist strips anything Research might add.
$released = array(
    'schema' => 'sc-research-public-article/1.0', 'slug' => 'metrics-public-overview', 'title' => 'Metrics',
    'listed' => true, 'summary' => 'S', 'area' => array('slug' => 'metrics', 'title' => 'Metrics', 'ownerScope' => 'PRIVATE_OWNER'),
    'revision' => 2, 'releasedAt' => '2026-11-16T00:00:00+00:00',
    'sections' => array(array('heading' => 'Overview', 'body' => 'Text', 'pin' => 'PRIVATE_PIN')),
    'references' => array(array('role' => 'dataset', 'status' => 'available', 'title' => 'Data', 'kind' => 'dataset',
                                'publicKey' => 'k', 'objectId' => 'PRIVATE_OBJECT', 'provenance' => '/x')),
    'publication' => array('type' => 'report', 'derivedFrom' => array(array('relation' => 'derived-from',
        'kind' => 'workspace-studio-revision', 'label' => 'Reporting Studio revision 3', 'workspaceProjectId' => 'PRIVATE_PROJECT',
        'documentSha256' => 'PRIVATE_SHA')),
        'bibliography' => array(array('key' => 'beerling2020', 'reference' => 'Beerling 2020', 'doi' => '10.1038/s41586-020-2448-9'),
                                array('key' => 'x', 'reference' => 'X', 'doi' => 'javascript:alert(1)'))),
    'classificationNotice' => null, 'ownerScope' => 'PRIVATE_OWNER',
);
fake_json_response($released);
$GLOBALS['sc_test_requests'] = array();
$got = sc_research_bridge_fetch_article('metrics-public-overview');
sc_assert(!is_wp_error($got) && $got[0] === 200, 'released article served');
sc_assert(str_ends_with($GLOBALS['sc_test_requests'][0]['url'], '/api/research/v1/articles/metrics-public-overview/content'),
    'article fetched from its fixed content route');
sc_assert(!str_contains(json_encode($got[1]), 'PRIVATE_'), 'owner scopes, pins, object ids and Workspace ids never pass');
sc_assert($got[1]['publication']['bibliography'][0]['doi'] === '10.1038/s41586-020-2448-9', 'DOI kept');
sc_assert($got[1]['publication']['bibliography'][1]['doi'] === '', 'non-DOI link dropped');
$GLOBALS['sc_test_requests'] = array();
sc_assert(is_wp_error(sc_research_bridge_fetch_article('../objects')), 'slug pattern enforced');
sc_assert(is_wp_error(sc_research_bridge_fetch_article('Metrics')), 'uppercase slug refused');
sc_assert(count($GLOBALS['sc_test_requests']) === 0, 'refused slugs never reach Research');
fake_json_response(array_merge($released, array('slug' => 'another-article')));
sc_assert(is_wp_error(sc_research_bridge_fetch_article('metrics-public-overview')), 'slug mismatch fails closed');
fake_json_response(array('schema' => 'sc-research-public-tombstone/1.0', 'slug' => 'metrics-public-overview',
    'status' => 'withdrawn', 'title' => 'Metrics', 'withdrawnAt' => '2026-11-16T01:00:00+00:00', 'notice' => 'Withdrawn.',
    'ownerScope' => 'PRIVATE_OWNER'), 410);
$gone = sc_research_bridge_fetch_article('metrics-public-overview');
sc_assert(!is_wp_error($gone) && $gone[0] === 410 && $gone[1]['status'] === 'withdrawn', 'withdrawal answers 410 tombstone');
sc_assert(!isset($gone[1]['sections']) && !str_contains(json_encode($gone[1]), 'PRIVATE_'), 'tombstone carries no content');
fake_json_response(array('detail' => 'Research article not found'), 404);
$missing = sc_research_bridge_fetch_article('metrics-public-overview');
sc_assert(is_wp_error($missing) && $missing->code === 'sc_research_article_not_found', 'unreleased article is 404');
fake_json_response(array('items' => array(array('slug' => 'metrics', 'title' => 'Metrics', 'summary' => 'S', 'owner' => 'PRIVATE_OWNER'),
    array('slug' => '../bad'))));
$areas = sc_research_bridge_fetch('areas');
sc_assert(count($areas['items']) === 1 && !str_contains(json_encode($areas), 'PRIVATE_'), 'areas projected');

$GLOBALS['sc_test_options']['sc_research_bridge_enabled'] = '0';
sc_assert(is_wp_error(sc_research_bridge_fetch('articles')), 'disable toggle is enforced instantly');
sc_assert(is_wp_error(sc_research_bridge_fetch_article('metrics-public-overview')), 'disabled bridge serves no article');

echo "PASS: WordPress Research bridge origin, settings, fail-closed routing and public-only projection\n";
