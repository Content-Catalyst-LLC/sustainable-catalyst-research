<?php
/**
 * Plugin Name: Sustainable Catalyst Research Bridge
 * Description: Read-only, fail-closed bridge from the existing Research WordPress page to the approved persistent Research API.
 * Version: 0.5.1
 * Requires PHP: 8.0
 * Author: Sustainable Catalyst
 * License: Proprietary
 *
 * RH-PROD-06 — Installs independently; makes no production gateway changes.
 * No private Workspace route, login, or source-file access is proxied.
 */

if (!defined('ABSPATH')) {
    exit;
}

const SC_RESEARCH_BRIDGE_VERSION = '0.5.1';
/** SC-FIG-03B: the Lab release whose renderer files the Research figure lock pins (same bytes, SRI). */
const SC_RESEARCH_FIGURES_LAB_RELEASE = '0.163.1.9';
/** Figure channels this bridge release may show. A production channel needs a later, approved release. */
const SC_RESEARCH_FIGURE_CHANNELS = array('staging-synthetic');
const SC_RESEARCH_FIGURE_MAX_FILE_BYTES = 8388608;
const SC_RESEARCH_BRIDGE_NAMESPACE = 'sc-research/v1';

/** Configuration constants take precedence; administrator settings require a nonce. */
function sc_research_bridge_enabled(): bool {
    if (defined('SC_RESEARCH_BRIDGE_ENABLED')) {
        return SC_RESEARCH_BRIDGE_ENABLED === true;
    }
    return get_option('sc_research_bridge_enabled', '0') === '1';
}

/**
 * Never take proxy hosts from requests, WordPress users, HTML or JavaScript.
 * HTTPS origin must be an explicitly configured Sustainable Catalyst subdomain,
 * different from the WordPress front-end. No IPs, ports, credentials or paths.
 */
function sc_research_bridge_normalize_origin($value): string {
    if (!is_string($value) || strlen($value) > 240) {
        return '';
    }
    $origin = rtrim(trim($value), '/');
    $parts = wp_parse_url($origin);
    if (!is_array($parts) || ($parts['scheme'] ?? '') !== 'https') {
        return '';
    }
    $host = strtolower($parts['host'] ?? '');
    if (!preg_match('/^[a-z0-9-]+(?:\.[a-z0-9-]+)*\.sustainablecatalyst\.com$/D', $host)) {
        return '';
    }
    foreach (array('port', 'user', 'pass', 'path', 'query', 'fragment') as $forbidden) {
        if (isset($parts[$forbidden])) {
            return '';
        }
    }
    if ($host === strtolower((string) wp_parse_url(home_url('/'), PHP_URL_HOST))) {
        return '';
    }
    return 'https://' . $host;
}

function sc_research_bridge_origin(): string {
    $staging = sc_research_bridge_staging_origin();
    if ($staging !== '') {
        return $staging;
    }
    $value = defined('SC_RESEARCH_API_ORIGIN')
        ? SC_RESEARCH_API_ORIGIN
        : get_option('sc_research_api_origin', '');
    return sc_research_bridge_normalize_origin($value);
}

/**
 * Isolated staging only: an internal plain-HTTP service name on the staging network, from a wp-config.php
 * constant, honoured only when WordPress itself declares a non-production environment. Never an option,
 * never on production; one DNS label, no port, path or credentials.
 */
function sc_research_bridge_staging_origin(): string {
    if (!defined('SC_RESEARCH_API_STAGING_ORIGIN') || !function_exists('wp_get_environment_type') ||
        !in_array(wp_get_environment_type(), array('local', 'development', 'staging'), true)) {
        return '';
    }
    $value = SC_RESEARCH_API_STAGING_ORIGIN;
    if (!is_string($value) || !preg_match('#^http://([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)$#D', $value, $m)) {
        return '';
    }
    return $value;
}

/** wp_safe_remote_get refuses internal hosts; allow exactly the configured staging service, nothing else. */
function sc_research_bridge_allow_staging_host($external, $host) {
    $staging = sc_research_bridge_staging_origin();
    if ($staging !== '' && is_string($host) && strtolower($host) === substr($staging, 7)) {
        return true;
    }
    return $external;
}
add_filter('http_request_host_is_external', 'sc_research_bridge_allow_staging_host', 10, 2);

function sc_research_bridge_error() {
    return new WP_Error(
        'sc_research_backend_unavailable',
        'Approved Research backend is not connected.',
        array('status' => 503)
    );
}

/** Only these four GET routes can ever reach the upstream Research API. */
function sc_research_bridge_paths(): array {
    return array(
        'status' => '/public/status',
        'articles' => '/articles',
        'objects' => '/objects?limit=50',
        'graph' => '/graph/metadata?limit=50',
    );
}

function sc_research_bridge_text($value, int $max = 350): string {
    return is_string($value) ? substr($value, 0, $max) : '';
}

function sc_research_bridge_project_object($row): ?array {
    if (!is_array($row) ||
        ($row['review_status'] ?? null) !== 'approved-public' ||
        ($row['rights'] ?? null) !== 'cleared' ||
        !is_string($row['id'] ?? null) ||
        !preg_match('/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/D', $row['id'])) {
        return null;
    }
    return array(
        'id' => $row['id'],
        'title' => sc_research_bridge_text($row['title'] ?? null),
        'summary' => sc_research_bridge_text($row['summary'] ?? null, 1400),
        'kind' => sc_research_bridge_text($row['kind'] ?? null, 48),
        'article' => sc_research_bridge_text($row['article'] ?? null, 150),
        'area' => sc_research_bridge_text($row['area'] ?? null, 130),
        'authority' => sc_research_bridge_text($row['authority'] ?? null, 64),
        'data_classification' => sc_research_bridge_text($row['data_classification'] ?? null, 30),
        'doi' => sc_research_bridge_text($row['doi'] ?? null, 240),
        'review_status' => 'approved-public',
        'rights' => 'cleared',
    );
}

/**
 * Field allowlist is an independent protection if an upstream response changes.
 * Original object IDs, source paths, owner names, revisions and private fields
 * are never returned by this WordPress bridge.
 */
function sc_research_bridge_project(string $route, $payload) {
    if (!is_array($payload)) {
        return sc_research_bridge_error();
    }

    if ($route === 'status') {
        if (($payload['schema'] ?? '') !== 'sc-research-public-backend-status/1.0' ||
            ($payload['source'] ?? '') !== 'research-postgresql' ||
            ($payload['storage'] ?? '') !== 'ready' ||
            !is_int($payload['approved_areas'] ?? null) ||
            !is_int($payload['approved_articles'] ?? null) ||
            $payload['approved_areas'] < 0 || $payload['approved_articles'] < 0) {
            return sc_research_bridge_error();
        }
        return array(
            'schema' => 'sc-research-public-backend-status/1.0',
            'source' => 'research-postgresql',
            'storage' => 'ready',
            'approved_areas' => $payload['approved_areas'],
            'approved_articles' => $payload['approved_articles'],
        );
    }

    if ($route === 'articles') {
        if (!isset($payload['items']) || !is_array($payload['items'])) {
            return sc_research_bridge_error();
        }
        $items = array();
        foreach (array_slice($payload['items'], 0, 50) as $row) {
            if (!is_array($row) || !is_string($row['slug'] ?? null) ||
                !preg_match('/^[a-z0-9]+(?:-[a-z0-9]+)*$/D', $row['slug'])) {
                continue;
            }
            $items[] = array(
                'slug' => $row['slug'],
                'title' => sc_research_bridge_text($row['title'] ?? null),
                'summary' => sc_research_bridge_text($row['summary'] ?? null, 1400),
                'area_slug' => sc_research_bridge_text($row['area_slug'] ?? null, 130),
                'state' => sc_research_bridge_text($row['state'] ?? null, 32),
                'version' => sc_research_bridge_text($row['version'] ?? null, 64),
            );
        }
        return array('items' => $items);
    }

    if ($route === 'objects') {
        if (($payload['schema'] ?? '') !== 'sc-research-public-objects/1.0' ||
            !is_array($payload['items'] ?? null)) {
            return sc_research_bridge_error();
        }
        $items = array();
        foreach (array_slice($payload['items'], 0, 50) as $row) {
            $item = sc_research_bridge_project_object($row);
            if ($item !== null) {
                $items[] = $item;
            }
        }
        return array('schema' => 'sc-research-public-objects/1.0', 'items' => $items);
    }

    if ($route === 'graph') {
        if (($payload['schema'] ?? '') !== 'sc-research-public-object-graph/1.0' ||
            !is_array($payload['nodes'] ?? null) ||
            !is_array($payload['edges'] ?? null)) {
            return sc_research_bridge_error();
        }
        $nodes = array();
        foreach (array_slice($payload['nodes'], 0, 50) as $row) {
            $node = sc_research_bridge_project_object($row);
            if ($node !== null) {
                $nodes[] = $node;
            }
        }
        $ids = array_fill_keys(array_column($nodes, 'id'), true);
        $edges = array();
        foreach (array_slice($payload['edges'], 0, 150) as $row) {
            if (!is_array($row) || !is_string($row['source'] ?? null) ||
                !is_string($row['target'] ?? null) ||
                !isset($ids[$row['source']], $ids[$row['target']])) {
                continue;
            }
            $edges[] = array(
                'source' => $row['source'],
                'target' => $row['target'],
                'relation' => sc_research_bridge_text($row['relation'] ?? null, 80),
                'note' => sc_research_bridge_text($row['note'] ?? null, 250),
            );
        }
        return array(
            'schema' => 'sc-research-public-object-graph/1.0',
            'nodes' => $nodes,
            'edges' => $edges,
        );
    }
    return sc_research_bridge_error();
}

/** One bounded GET to the approved upstream. Returns array(status, content-type, body) or WP_Error. */
function sc_research_bridge_upstream(string $path, int $limit, string $accept) {
    $origin = sc_research_bridge_origin();
    if (!sc_research_bridge_enabled() || $origin === '') {
        return sc_research_bridge_error();
    }
    $result = wp_safe_remote_get($origin . '/api/research/v1' . $path, array(
        'timeout' => 6,
        'redirection' => 0,
        'sslverify' => true,
        'cookies' => array(),
        'headers' => array('Accept' => $accept),
        'limit_response_size' => $limit,
    ));
    if (is_wp_error($result)) {
        return sc_research_bridge_error();
    }
    $body = wp_remote_retrieve_body($result);
    if (!is_string($body) || strlen($body) >= $limit) {
        return sc_research_bridge_error();
    }
    return array((int) wp_remote_retrieve_response_code($result),
        strtolower((string) wp_remote_retrieve_header($result, 'content-type')), $body);
}

function sc_research_bridge_fetch(string $route) {
    $paths = sc_research_bridge_paths();
    if (!sc_research_bridge_enabled() || !isset($paths[$route])) {
        return sc_research_bridge_error();
    }
    $got = sc_research_bridge_upstream($paths[$route], 200001, 'application/json');
    if (is_wp_error($got) || $got[0] !== 200 || strpos($got[1], 'application/json') === false) {
        return sc_research_bridge_error();
    }
    return sc_research_bridge_project($route, json_decode($got[2], true));
}

// ---------------------------------------------------------------------------------------------------
// SC-FIG-03B: released scientific figures. Research is the release authority and re-verifies every
// request; this facade adds an independent field allowlist, a channel gate and no caching of any kind.

function sc_research_figures_channel(): string {
    $value = defined('SC_RESEARCH_FIGURES_CHANNEL') ? SC_RESEARCH_FIGURES_CHANNEL
        : get_option('sc_research_figures_channel', '');
    return (is_string($value) && in_array($value, SC_RESEARCH_FIGURE_CHANNELS, true)) ? $value : '';
}

/** The installed Lab plugin must be the exact release the figure renderer lock pins. */
function sc_research_figures_lab_ready(): bool {
    return defined('SC_LAB_RELEASE_VERSION') && SC_LAB_RELEASE_VERSION === SC_RESEARCH_FIGURES_LAB_RELEASE;
}

/**
 * SC-FIG-04.1: this bridge and the Lab release it pins are installed as a pair. Activating the bridge beside
 * another active Lab release, or activating another Lab release beside this bridge, is refused (409);
 * a mismatch created by replacing files keeps figures off (sc_research_figures_lab_ready) and is shown
 * to administrators. The Lab plugin is recognised by its header name, wherever it is installed.
 */
const SC_RESEARCH_LAB_PLUGIN_NAME = 'Sustainable Catalyst Lab';

function sc_research_bridge_pairing_message(string $lab_version): string {
    return sprintf('Sustainable Catalyst Research Bridge %s requires Sustainable Catalyst Lab %s; Lab %s is not compatible. Install the matching pair (bridge 0.3.x with Lab 0.163.1.7, bridge 0.4.x with Lab 0.163.1.8, bridge 0.5.x with Lab 0.163.1.9).',
        SC_RESEARCH_BRIDGE_VERSION, SC_RESEARCH_FIGURES_LAB_RELEASE, $lab_version);
}

function sc_research_bridge_refuse_pairing(string $lab_version): void {
    wp_die(esc_html(sc_research_bridge_pairing_message($lab_version)), 'Incompatible plugin combination', array('response' => 409, 'back_link' => true));
}

/** The active Lab release when it is not the pinned one, else null (no Lab active is not a mismatch). */
function sc_research_bridge_lab_mismatch(): ?string {
    if (!defined('SC_LAB_RELEASE_VERSION')) {
        return null;
    }
    return SC_LAB_RELEASE_VERSION === SC_RESEARCH_FIGURES_LAB_RELEASE ? null : (string) SC_LAB_RELEASE_VERSION;
}

function sc_research_bridge_on_activate(): void {
    $lab = sc_research_bridge_lab_mismatch();
    if ($lab !== null) {
        sc_research_bridge_refuse_pairing($lab);
    }
}
register_activation_hook(__FILE__, 'sc_research_bridge_on_activate');

function sc_research_bridge_on_plugin_activation($plugin): void {
    $file = WP_PLUGIN_DIR . '/' . ltrim((string) $plugin, '/');
    if (!is_file($file)) {
        return;
    }
    $h = get_file_data($file, array('Name' => 'Plugin Name', 'Version' => 'Version'));
    if (is_array($h) && $h['Name'] === SC_RESEARCH_LAB_PLUGIN_NAME && $h['Version'] !== SC_RESEARCH_FIGURES_LAB_RELEASE) {
        sc_research_bridge_refuse_pairing((string) $h['Version']);
    }
}
add_action('activate_plugin', 'sc_research_bridge_on_plugin_activation', 1, 1);

add_action('admin_notices', function (): void {
    $lab = sc_research_bridge_lab_mismatch();
    if ($lab !== null && current_user_can('activate_plugins')) {
        echo '<div class="notice notice-error"><p>' . esc_html(sc_research_bridge_pairing_message($lab) . ' Research figures stay off until then.') . '</p></div>';
    }
});

function sc_research_figures_enabled(): bool {
    return sc_research_bridge_enabled() && sc_research_bridge_origin() !== '' &&
        sc_research_figures_channel() !== '' && sc_research_figures_lab_ready();
}

function sc_research_figures_not_found() {
    return new WP_Error('sc_research_figure_not_found', 'Research figure not found.', array('status' => 404));
}

function sc_research_figures_uuid($value): bool {
    return is_string($value) && preg_match('/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/D', $value) === 1;
}

function sc_research_figures_sha($value): bool {
    return is_string($value) && preg_match('/^[0-9a-f]{64}$/D', $value) === 1;
}

function sc_research_figures_prefix(): string {
    $path = wp_parse_url(rest_url(SC_RESEARCH_BRIDGE_NAMESPACE), PHP_URL_PATH);
    return is_string($path) ? rtrim($path, '/') : '';
}

function sc_research_figures_status() {
    $empty = array('schema' => 'sc-research-public-figure-status/1.0', 'enabled' => false, 'channel' => null, 'label' => null);
    if (!sc_research_figures_enabled()) {
        return $empty;
    }
    $got = sc_research_bridge_upstream('/public/figures/status', 20001, 'application/json');
    if (is_wp_error($got) || $got[0] !== 200 || strpos($got[1], 'application/json') === false) {
        return sc_research_bridge_error();
    }
    $p = json_decode($got[2], true);
    if (!is_array($p) || ($p['schema'] ?? '') !== 'sc-research-public-figure-status/1.0') {
        return sc_research_bridge_error();
    }
    // Upstream and WordPress must name the same channel; anything else shows no figures.
    if (($p['enabled'] ?? false) !== true || ($p['channel'] ?? '') !== sc_research_figures_channel()) {
        return $empty;
    }
    return array('schema' => 'sc-research-public-figure-status/1.0', 'enabled' => true,
        'channel' => sc_research_figures_channel(), 'label' => sc_research_bridge_text($p['label'] ?? null, 200));
}

const SC_RESEARCH_FIGURE_MEDIA = array(
    'application/json', 'application/vnd.sc.figure-bundle.v1+json', 'application/vnd.plotly.v1+json',
    'application/vnd.vegalite.v5+json', 'image/svg+xml', 'image/png',
    // SC-FIG-04: Lab MapLibre map views (Site Intelligence evidence objects; inline GeoJSON only).
    'application/vnd.sc.maplibre-view.v1+json',
);

/** Explicit projection of sc-research-public-figure/2.0; file URLs are rewritten to this facade. */
function sc_research_figures_project(string $id, $p) {
    if (!is_array($p) || ($p['schema'] ?? '') !== 'sc-research-public-figure/2.0' || ($p['id'] ?? '') !== $id) {
        return sc_research_bridge_error();
    }
    $r = is_array($p['release'] ?? null) ? $p['release'] : array();
    $ctx = is_array($p['releaseContext'] ?? null) ? $p['releaseContext'] : array();
    if (($r['authority'] ?? '') !== 'sc-research' || ($r['channel'] ?? '') !== sc_research_figures_channel() ||
        !sc_research_figures_sha($r['bundleSha256'] ?? null) || ($ctx['authority'] ?? '') !== 'sc-research' ||
        ($ctx['bundleSha256'] ?? '') !== $r['bundleSha256'] || !is_int($r['articleRevision'] ?? null)) {
        return sc_research_bridge_error();
    }
    $t = 'sc_research_bridge_text';
    $files = array();
    $base = sc_research_figures_prefix() . '/figures/' . $id . '/files/';
    foreach (array_slice(is_array($p['files'] ?? null) ? $p['files'] : array(), 0, 12) as $f) {
        if (!is_array($f) || !sc_research_figures_sha($f['sha256'] ?? null) ||
            !in_array($f['role'] ?? '', array('bundle', 'view', 'runtime-record'), true) ||
            !in_array($f['mediaType'] ?? '', SC_RESEARCH_FIGURE_MEDIA, true) ||
            // SC-FIG-05.3: a runtime uncertainty record is plain JSON; the Lab renderer checks it against the bundle.
            (($f['role'] ?? '') === 'runtime-record' && ($f['mediaType'] ?? '') !== 'application/json') ||
            !is_int($f['bytes'] ?? null) || $f['bytes'] < 1 || $f['bytes'] > SC_RESEARCH_FIGURE_MAX_FILE_BYTES) {
            return sc_research_bridge_error();
        }
        $files[] = array('role' => $f['role'], 'sha256' => $f['sha256'], 'mediaType' => $f['mediaType'],
            'bytes' => $f['bytes'], 'url' => $base . $f['sha256']);
    }
    if (!$files) {
        return sc_research_bridge_error();
    }
    $citations = array();
    foreach (array_slice(is_array($p['citations'] ?? null) ? $p['citations'] : array(), 0, 50) as $c) {
        if (!is_array($c)) {
            continue;
        }
        $row = array();
        foreach (array('sourceId', 'provider', 'doi', 'citationId', 'identityStatus', 'qualityStatus', 'licenceStatus') as $k) {
            if (is_string($c[$k] ?? null)) {
                $row[$k] = $t($c[$k], 240);
            }
        }
        $citations[] = $row;
    }
    $dims = array_values(array_intersect(array('scientific', 'ownership', 'rights', 'publication'),
        is_array($r['approvals']['dimensions'] ?? null) ? $r['approvals']['dimensions'] : array()));
    $hash = static function ($v) { return sc_research_figures_sha($v) ? $v : null; };
    return array(
        'schema' => 'sc-research-public-figure/2.0', 'id' => $id,
        'article' => $t($p['article'] ?? null, 150), 'area' => $t($p['area'] ?? null, 200),
        'title' => $t($p['title'] ?? null), 'figureId' => $t($p['figureId'] ?? null, 120),
        'version' => is_int($p['version'] ?? null) ? $p['version'] : 1,
        'evidenceClass' => $t($p['evidenceClass'] ?? null, 40), 'measurementStatus' => $t($p['measurementStatus'] ?? null, 40),
        'dataClassification' => $t($p['dataClassification'] ?? null, 30),
        'classificationNotice' => $t($p['classificationNotice'] ?? null, 400),
        'release' => array(
            'channel' => $r['channel'], 'label' => $t($r['label'] ?? null, 200), 'authority' => 'sc-research',
            'bundleSha256' => $r['bundleSha256'], 'provenanceSha256' => $hash($r['provenanceSha256'] ?? null),
            'registryVersion' => $t($r['registryVersion'] ?? null, 120), 'articleRevision' => $r['articleRevision'],
            'releasedAt' => $t($r['releasedAt'] ?? null, 40), 'releaseSha256' => $hash($r['releaseSha256'] ?? null),
            'approvals' => array('dimensions' => $dims, 'authority' => 'Sustainable Catalyst Workspace',
                'signed' => ($r['approvals']['signed'] ?? false) === true, 'boundTo' => 'provenanceSha256'),
            'approval' => $t($r['approval'] ?? null, 400), 'reproducibility' => $t($r['reproducibility'] ?? null, 80),
        ),
        'releaseContext' => array(
            'authority' => 'sc-research', 'state' => ($ctx['state'] ?? '') === 'released' ? 'released' : 'invalid',
            'label' => $t($ctx['label'] ?? null, 200), 'bundleSha256' => $r['bundleSha256'],
            'articleRevision' => $r['articleRevision'], 'releasedAt' => $t($ctx['releasedAt'] ?? null, 40),
        ),
        'units' => $t($p['units'] ?? null, 200), 'uncertainty' => $t($p['uncertainty'] ?? null, 200),
        'citations' => $citations, 'files' => $files, 'missingViews' => array(),
    );
}

function sc_research_figures_package(string $id) {
    if (!sc_research_figures_enabled() || !sc_research_figures_uuid($id)) {
        return sc_research_figures_not_found();
    }
    $got = sc_research_bridge_upstream('/objects/' . $id . '/figure', 200001, 'application/json');
    if (is_wp_error($got)) {
        return $got;
    }
    if ($got[0] === 404) {
        return sc_research_figures_not_found();  // unreleased, withdrawn or never public: same answer
    }
    if ($got[0] !== 200 || strpos($got[1], 'application/json') === false) {
        return sc_research_bridge_error();
    }
    return sc_research_figures_project($id, json_decode($got[2], true));
}

/** Exact released bytes; the browser renderer re-verifies them against the release hash. */
function sc_research_figures_file(string $id, string $sha) {
    if (!sc_research_figures_enabled() || !sc_research_figures_uuid($id) || !sc_research_figures_sha($sha)) {
        return sc_research_figures_not_found();
    }
    $got = sc_research_bridge_upstream('/objects/' . $id . '/figure/files/' . $sha,
        SC_RESEARCH_FIGURE_MAX_FILE_BYTES + 1, 'application/octet-stream');
    if (is_wp_error($got)) {
        return $got;
    }
    if ($got[0] === 404) {
        return sc_research_figures_not_found();
    }
    if ($got[0] !== 200 || strpos($got[1], 'application/octet-stream') !== 0 || $got[2] === '') {
        return sc_research_bridge_error();
    }
    return $got[2];
}

function sc_research_figures_headers(WP_REST_Response $response): WP_REST_Response {
    $response->header('Cache-Control', 'no-store');
    $response->header('X-Content-Type-Options', 'nosniff');
    return $response;
}

function sc_research_figures_register_routes(): void {
    $uuid = '(?P<id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})';
    register_rest_route(SC_RESEARCH_BRIDGE_NAMESPACE, '/figures/status', array(
        'methods' => 'GET', 'permission_callback' => '__return_true',
        'callback' => static function () {
            $result = sc_research_figures_status();
            return is_wp_error($result) ? $result : sc_research_figures_headers(new WP_REST_Response($result, 200));
        },
    ));
    register_rest_route(SC_RESEARCH_BRIDGE_NAMESPACE, '/figures/' . $uuid, array(
        'methods' => 'GET', 'permission_callback' => '__return_true',
        'callback' => static function ($request) {
            $result = sc_research_figures_package((string) $request['id']);
            return is_wp_error($result) ? $result : sc_research_figures_headers(new WP_REST_Response($result, 200));
        },
    ));
    register_rest_route(SC_RESEARCH_BRIDGE_NAMESPACE, '/figures/' . $uuid . '/files/(?P<sha>[0-9a-f]{64})', array(
        'methods' => 'GET', 'permission_callback' => '__return_true',
        'callback' => static function ($request) {
            $bytes = sc_research_figures_file((string) $request['id'], (string) $request['sha']);
            if (is_wp_error($bytes)) {
                return $bytes;
            }
            $response = sc_research_figures_headers(new WP_REST_Response(array('sc_research_raw' => $bytes), 200));
            $response->header('Content-Type', 'application/octet-stream');
            $response->header('Content-Disposition', 'attachment');
            $response->header('Content-Security-Policy', "default-src 'none'; sandbox");
            return $response;
        },
    ));
}
add_action('rest_api_init', 'sc_research_figures_register_routes');

/** Serve figure file bytes as they are (the REST server would otherwise JSON-encode them). */
function sc_research_figures_serve_raw($served, $result, $request) {
    if ($served || !($result instanceof WP_REST_Response) || $result->get_status() !== 200) {
        return $served;
    }
    $data = $result->get_data();
    if (!is_array($data) || !isset($data['sc_research_raw']) || count($data) !== 1 ||
        strpos((string) $request->get_route(), '/' . SC_RESEARCH_BRIDGE_NAMESPACE . '/figures/') !== 0) {
        return $served;
    }
    echo $data['sc_research_raw']; // phpcs:ignore WordPress.Security.EscapeOutput -- octet-stream attachment, sandboxed
    return true;
}
add_filter('rest_pre_serve_request', 'sc_research_figures_serve_raw', 10, 3);

function sc_research_bridge_register_routes(): void {
    foreach (array_keys(sc_research_bridge_paths()) as $key) {
        register_rest_route(SC_RESEARCH_BRIDGE_NAMESPACE, '/' . $key, array(
            'methods' => 'GET',
            'permission_callback' => '__return_true',
            'callback' => static function () use ($key) {
                $result = sc_research_bridge_fetch($key);
                if (is_wp_error($result)) {
                    return $result;
                }
                $response = new WP_REST_Response($result, 200);
                $response->header('Cache-Control', 'no-store');
                $response->header('X-Content-Type-Options', 'nosniff');
                return $response;
            },
        ));
    }
}
add_action('rest_api_init', 'sc_research_bridge_register_routes');

function sc_research_bridge_assets(): void {
    // This plugin augments only the original Research page, not other site apps.
    if (!is_page('research-hub')) {
        return;
    }
    $css = plugins_url('assets/research-workbench-bridge.css', __FILE__);
    $js = plugins_url('assets/research-workbench-bridge.js', __FILE__);
    wp_enqueue_style('sc-research-public-bridge', $css, array(), SC_RESEARCH_BRIDGE_VERSION);
    wp_enqueue_script('sc-research-public-bridge', $js, array(), SC_RESEARCH_BRIDGE_VERSION, true);
    // Restore original WordPress workbench tabs and local preview interaction.
    // Kept external to page content so WordPress autoformatting cannot corrupt it.
    wp_enqueue_script(
        'sc-research-navigation',
        plugins_url('assets/research-navigation.js', __FILE__),
        array('sc-research-public-bridge'), SC_RESEARCH_BRIDGE_VERSION, true
    );

    // Bounded same-origin URL, not a user-editable cross-site API hostname.
    $prefix = sc_research_figures_prefix();
    $config = array(
        'enabled' => sc_research_bridge_enabled() && sc_research_bridge_origin() !== '',
        'apiPrefix' => $prefix,
    );
    if (sc_research_figures_enabled()) {
        // The renderer runs from the installed Lab plugin: same files, verified by the Research lock (SRI).
        $vendor = wp_parse_url(plugins_url('sustainable-catalyst-lab/assets/'), PHP_URL_PATH);
        $config['figures'] = array('enabled' => true, 'channel' => sc_research_figures_channel(),
            'labRelease' => SC_RESEARCH_FIGURES_LAB_RELEASE, 'rendererBase' => is_string($vendor) ? $vendor : '');
        wp_enqueue_script('sc-research-figures', plugins_url('assets/research-figures.js', __FILE__),
            array('sc-research-public-bridge'), SC_RESEARCH_BRIDGE_VERSION, true);
        wp_enqueue_script('sc-research-hub-figures', plugins_url('assets/research-hub-figures.js', __FILE__),
            array('sc-research-figures'), SC_RESEARCH_BRIDGE_VERSION, true);
    }
    wp_add_inline_script(
        'sc-research-public-bridge',
        'window.SustainableCatalystResearchConfig=' .
            wp_json_encode($config, JSON_HEX_TAG | JSON_HEX_AMP | JSON_HEX_APOS | JSON_HEX_QUOT) . ';',
        'before'
    );
}
add_action('wp_enqueue_scripts', 'sc_research_bridge_assets');

/**
 * Website administrators can configure the approved public API without an SSH
 * terminal. Protected wp-config.php constants (when present) always win.
 * The form contains no passwords, access tokens, private routes or user IDs.
 */
function sc_research_bridge_save_settings(): void {
    if (!current_user_can('manage_options')) {
        wp_die(esc_html__('Insufficient permissions.', 'sc-research-bridge'), '', array('response' => 403));
    }
    check_admin_referer('sc_research_bridge_save');

    $origin = sc_research_bridge_origin();
    if (!defined('SC_RESEARCH_API_ORIGIN')) {
        $submitted = isset($_POST['sc_research_api_origin']) && is_string($_POST['sc_research_api_origin'])
            ? sanitize_text_field(wp_unslash($_POST['sc_research_api_origin'])) : '';
        $origin = sc_research_bridge_normalize_origin($submitted);
        if ($submitted !== '' && $origin === '') {
            wp_safe_redirect(add_query_arg('sc_research_notice', 'invalid_origin', admin_url('tools.php?page=sc-research-bridge')));
            exit;
        }
        update_option('sc_research_api_origin', $origin, false);
    }
    if (!defined('SC_RESEARCH_BRIDGE_ENABLED')) {
        $requested = isset($_POST['sc_research_bridge_enabled']) && $_POST['sc_research_bridge_enabled'] === '1';
        update_option('sc_research_bridge_enabled', ($requested && $origin !== '') ? '1' : '0', false);
    }
    wp_safe_redirect(add_query_arg('sc_research_notice', 'saved', admin_url('tools.php?page=sc-research-bridge')));
    exit;
}
add_action('admin_post_sc_research_bridge_save', 'sc_research_bridge_save_settings');

/** Administrator-only operational visibility; never display credentials. */
function sc_research_bridge_admin_menu(): void {
    add_management_page(
        'Research Bridge', 'Research Bridge', 'manage_options',
        'sc-research-bridge', 'sc_research_bridge_admin_page'
    );
}
add_action('admin_menu', 'sc_research_bridge_admin_menu');

function sc_research_bridge_admin_page(): void {
    if (!current_user_can('manage_options')) {
        return;
    }

    $enabled = sc_research_bridge_enabled();
    $origin = sc_research_bridge_origin();
    $ready = $enabled && $origin !== '';
    $status = $ready ? sc_research_bridge_fetch('status') : sc_research_bridge_error();
    $connected = !is_wp_error($status)
        && ($status['schema'] ?? '') === 'sc-research-public-backend-status/1.0'
        && ($status['source'] ?? '') === 'research-postgresql'
        && ($status['storage'] ?? '') === 'ready';

    echo '<div class="wrap"><h1>Research Backend Bridge</h1>';
    echo '<p>Public Research website integration. Workspace handles private login, project ownership and authoring. No credentials are stored by this plugin.</p>';
    if (isset($_GET['sc_research_notice']) && $_GET['sc_research_notice'] === 'saved') {
        echo '<div class="notice notice-success"><p>Research bridge settings saved.</p></div>';
    }
    if (isset($_GET['sc_research_notice']) && $_GET['sc_research_notice'] === 'invalid_origin') {
        echo '<div class="notice notice-error"><p>Origin rejected. Use an approved HTTPS Sustainable Catalyst subdomain without a port or path.</p></div>';
    }

    echo '<h2>Live connection test</h2><p><strong>Status:</strong> ' .
        esc_html($connected ? 'Connected — verified Research PostgreSQL' :
            ($ready ? 'Disconnected — backend not responding with verified persistent data' : 'Disabled — no backend requests')) .
        '</p>';
    if ($connected) {
        echo '<p>Approved research areas: ' . esc_html((string) $status['approved_areas']) .
            ' · Approved articles: ' . esc_html((string) $status['approved_articles']) .
            '. These counts are from the backend, not the editorial preview.</p>';
    }

    echo '<h2>Public API settings</h2><form method="post" action="' . esc_url(admin_url('admin-post.php')) . '">';
    wp_nonce_field('sc_research_bridge_save');
    echo '<input type="hidden" name="action" value="sc_research_bridge_save" />';
    echo '<table class="form-table"><tbody><tr><th scope="row"><label for="sc_research_api_origin">Approved public Research API origin</label></th><td>';
    echo '<input type="url" id="sc_research_api_origin" name="sc_research_api_origin" class="regular-text" ' .
        'value="' . esc_attr($origin) . '" placeholder="https://research-api.sustainablecatalyst.com" ' .
        (defined('SC_RESEARCH_API_ORIGIN') ? 'disabled ' : '') . '/>';
    echo '<p class="description">HTTPS Sustainable Catalyst subdomain only. Must expose approved public Research endpoints, not private Workspace APIs.</p></td></tr>';
    echo '<tr><th scope="row">Connect public Research page</th><td>';
    echo '<label><input type="checkbox" name="sc_research_bridge_enabled" value="1" ' .
        checked($enabled, true, false) . (defined('SC_RESEARCH_BRIDGE_ENABLED') ? ' disabled ' : '') .
        '/> Enable live approved metadata</label></td></tr></tbody></table>';
    if (defined('SC_RESEARCH_BRIDGE_ENABLED') || defined('SC_RESEARCH_API_ORIGIN')) {
        echo '<p>One or more settings are controlled by wp-config.php and cannot be changed here.</p>';
    }
    submit_button('Save Research bridge settings');
    echo '</form>';
    echo '<p>Connection test repeats when this page is refreshed. The original public Research preview remains available during outages. ' .
        'Only approved read-only data are exposed, and publication rights remain controlled by Research.</p></div>';
}
