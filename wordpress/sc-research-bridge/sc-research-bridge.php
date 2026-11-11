<?php
/**
 * Plugin Name: Sustainable Catalyst Research Bridge
 * Description: Read-only, fail-closed bridge from the existing Research WordPress page to the approved persistent Research API.
 * Version: 0.2.4
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

const SC_RESEARCH_BRIDGE_VERSION = '0.2.4';
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
    $value = defined('SC_RESEARCH_API_ORIGIN')
        ? SC_RESEARCH_API_ORIGIN
        : get_option('sc_research_api_origin', '');
    return sc_research_bridge_normalize_origin($value);
}

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

function sc_research_bridge_fetch(string $route) {
    $paths = sc_research_bridge_paths();
    if (!sc_research_bridge_enabled() || !isset($paths[$route])) {
        return sc_research_bridge_error();
    }
    $origin = sc_research_bridge_origin();
    if ($origin === '') {
        return sc_research_bridge_error();
    }
    $url = $origin . '/api/research/v1' . $paths[$route];
    $result = wp_safe_remote_get($url, array(
        'timeout' => 6,
        'redirection' => 0,
        'sslverify' => true,
        'cookies' => array(),
        'headers' => array('Accept' => 'application/json'),
        'limit_response_size' => 200000,
    ));
    if (is_wp_error($result) || wp_remote_retrieve_response_code($result) !== 200 ||
        stripos((string) wp_remote_retrieve_header($result, 'content-type'), 'application/json') === false) {
        return sc_research_bridge_error();
    }
    $body = wp_remote_retrieve_body($result);
    if (!is_string($body) || strlen($body) > 200000) {
        return sc_research_bridge_error();
    }
    return sc_research_bridge_project($route, json_decode($body, true));
}

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
    $route_path = wp_parse_url(rest_url(SC_RESEARCH_BRIDGE_NAMESPACE), PHP_URL_PATH);
    $prefix = is_string($route_path) ? rtrim($route_path, '/') : '';
    $config = array(
        'enabled' => sc_research_bridge_enabled() && sc_research_bridge_origin() !== '',
        'apiPrefix' => $prefix,
    );
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
