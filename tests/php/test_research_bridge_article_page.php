<?php
/* RH-PUB-01 (bridge 0.6.1): the Hub page for one article states its own indexing, decided server-side from
 * Research's answer, and is never cached. Narrow WordPress stubs; no live WordPress or Research host.
 */
error_reporting(E_ALL);
define('ABSPATH', __DIR__ . '/');
$GLOBALS['sc_test_options'] = array('sc_research_api_origin' => 'https://research-api.sustainablecatalyst.com',
                                    'sc_research_bridge_enabled' => '1');
$GLOBALS['sc_test_requests'] = array();
$GLOBALS['sc_test_responses'] = array();
$GLOBALS['sc_test_hooks'] = array();
$GLOBALS['sc_test_is_hub'] = true;
$GLOBALS['sc_test_nocache'] = 0;

class WP_Error {
    public string $code;
    public function __construct(string $code, string $message = '', array $data = array()) { $this->code = $code; }
}
function is_wp_error($value): bool { return $value instanceof WP_Error; }
function add_action($hook, $callback, $priority = 10, $args = 1): void { $GLOBALS['sc_test_hooks'][$hook][] = $callback; }
function add_filter($hook, $callback, $priority = 10, $args = 1): void { $GLOBALS['sc_test_hooks'][$hook][] = $callback; }
function register_activation_hook($file, $callback): void {}
function get_option($name, $fallback = '') { return $GLOBALS['sc_test_options'][$name] ?? $fallback; }
function home_url($path = '') { return 'https://sustainablecatalyst.com/' . ltrim($path, '/'); }
function wp_parse_url($url, $component = -1) { return parse_url($url, $component); }
function add_query_arg($key, $value, $url) { return $url . (str_contains($url, '?') ? '&' : '?') . $key . '=' . rawurlencode($value); }
function is_page($slug) { return $GLOBALS['sc_test_is_hub'] && $slug === 'research-hub'; }
function nocache_headers() { $GLOBALS['sc_test_nocache']++; }
function wp_safe_remote_get($url, $args) {
    $GLOBALS['sc_test_requests'][] = $url;
    return array_shift($GLOBALS['sc_test_responses']) ?? new WP_Error('http_request_failed');
}
function wp_remote_retrieve_response_code($result) { return $result['code'] ?? 0; }
function wp_remote_retrieve_header($result, $name) { return $result['headers'][$name] ?? ''; }
function wp_remote_retrieve_body($result) { return $result['body'] ?? ''; }

require dirname(__DIR__, 2) . '/wordpress/sc-research-bridge/sc-research-bridge.php';

function sc_assert($condition, string $message): void {
    if (!$condition) { fwrite(STDERR, "FAIL: " . $message . "\n"); exit(1); }
}
function respond(int $status, array $payload): void {
    $GLOBALS['sc_test_responses'][] = array('code' => $status,
        'headers' => array('content-type' => 'application/json'), 'body' => json_encode($payload));
}
function article(string $slug, bool $listed): array {
    return array('schema' => 'sc-research-public-article/1.0', 'slug' => $slug, 'revision' => 2, 'listed' => $listed,
                 'title' => 'Synthetic article', 'summary' => 'A synthetic summary.', 'sections' => array(),
                 'area' => array('slug' => 'a', 'title' => 'A'), 'ownerScope' => 'NEVER');
}
function apply(string $hook, $value) {
    foreach ($GLOBALS['sc_test_hooks'][$hook] ?? array() as $cb) { $value = $cb($value); }
    return $value;
}
/** One page view: fresh per-request state, then every hook the page renders through. */
function view(?string $article): array {
    static $n = 0;
    $_GET = $article === null ? array() : array('article' => $article);
    $GLOBALS['sc_test_nocache'] = 0;
    foreach ($GLOBALS['sc_test_hooks']['template_redirect'] as $cb) { $cb(); }  // sends nocache + the headers below
    return array('noindexHeader' => sc_research_bridge_article_page_headers() === array('X-Robots-Tag: noindex'),
                 'nocache' => $GLOBALS['sc_test_nocache'],
                 'wp' => apply('wp_robots', array('index' => true, 'follow' => true)),
                 'rm' => apply('rank_math/frontend/robots', array('index' => 'index', 'follow' => 'follow')),
                 'canonical' => apply('rank_math/frontend/canonical', 'https://sustainablecatalyst.com/research-hub/'),
                 'core' => apply('get_canonical_url', 'https://sustainablecatalyst.com/research-hub/'),
                 'title' => apply('rank_math/frontend/title', 'Research Hub'),
                 'desc' => apply('rank_math/frontend/description', 'Hub description'));
}
function noindexed(array $v): bool {
    return $v['noindexHeader'] && !empty($v['wp']['noindex']) && !isset($v['wp']['index'])
        && $v['rm']['index'] === 'noindex' && $v['rm']['follow'] === 'follow'
        && $v['canonical'] === HUB && $v['core'] === HUB && $v['title'] === 'Research Hub';
}
const HUB = 'https://sustainablecatalyst.com/research-hub/';

// The Hub itself: untouched, cacheable, no Research request.
$v = view(null);
sc_assert(!$v['noindexHeader'] && $v['nocache'] === 0 && !isset($v['wp']['noindex']) && $v['rm']['index'] === 'index'
          && $v['canonical'] === HUB && count($GLOBALS['sc_test_requests']) === 0, 'Hub page without ?article= unchanged');

// Listed, released article: indexable, own canonical, title and summary, never cached, one upstream request.
respond(200, article('listed-one', true));
$v = view('listed-one');
sc_assert(!$v['noindexHeader'] && !isset($v['wp']['noindex']) && $v['rm']['index'] === 'index', 'listed article indexable');
sc_assert($v['canonical'] === HUB . '?article=listed-one' && $v['core'] === $v['canonical'], 'listed article canonical is its own URL');
sc_assert($v['title'] === 'Synthetic article · Research · Sustainable Catalyst' && $v['desc'] === 'A synthetic summary.', 'listed title/summary');
sc_assert($v['nocache'] === 1 && defined('DONOTCACHEPAGE') && DONOTCACHEPAGE === true, 'article page never cached');
sc_assert(count($GLOBALS['sc_test_requests']) === 1 && str_ends_with($GLOBALS['sc_test_requests'][0], '/articles/listed-one/content'),
          'state fetched once per request from the public content route');

// Unlisted: reachable, but noindex and the Hub as canonical.
respond(200, article('unlisted-one', false));
sc_assert(noindexed(view('unlisted-one')), 'unlisted article noindex, Hub canonical');

// Withdrawn: 410 tombstone -> noindex.
respond(410, array('schema' => 'sc-research-public-tombstone/1.0', 'slug' => 'gone-one', 'notice' => 'Withdrawn.'));
sc_assert(noindexed(view('gone-one')), 'withdrawn article noindex');

// Unknown, invalid slug, wrong slug in payload, outage, upstream error, bridge disabled: all fail closed.
respond(404, array('detail' => 'Research article not found'));
sc_assert(noindexed(view('no-such-article')), 'unknown article noindex');
$before = count($GLOBALS['sc_test_requests']);
foreach (array('Bad_Slug', '../x', '', str_repeat('a', 151)) as $bad) {
    sc_assert(noindexed(view($bad)), 'invalid slug noindex: ' . substr($bad, 0, 12));
}
sc_assert(count($GLOBALS['sc_test_requests']) === $before, 'invalid slugs never reach Research');
respond(200, article('other-slug', true));
sc_assert(noindexed(view('asked-slug')), 'payload for another slug is refused');
sc_assert(noindexed(view('outage-one')), 'Research outage noindex');  // no queued response -> WP_Error
respond(503, array('detail' => 'unavailable'));
sc_assert(noindexed(view('error-one')), 'upstream 503 noindex');
$GLOBALS['sc_test_options']['sc_research_bridge_enabled'] = '0';
$before = count($GLOBALS['sc_test_requests']);
sc_assert(noindexed(view('listed-two')) && count($GLOBALS['sc_test_requests']) === $before, 'disabled bridge noindex without a request');
$GLOBALS['sc_test_options']['sc_research_bridge_enabled'] = '1';

// Other pages with ?article= are not touched.
$GLOBALS['sc_test_is_hub'] = false;
$before = count($GLOBALS['sc_test_requests']);
$v = view('listed-one');
sc_assert(!$v['noindexHeader'] && $v['nocache'] === 0 && $v['canonical'] === HUB && count($GLOBALS['sc_test_requests']) === $before,
          'pages other than the Research Hub are untouched');

echo "PASS research bridge article page indexing\n";
