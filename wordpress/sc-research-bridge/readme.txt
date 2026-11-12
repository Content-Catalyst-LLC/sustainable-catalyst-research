=== Sustainable Catalyst Research Bridge ===
Contributors: sustainable-catalyst
Tags: research, public metadata, backend, research publications
Requires at least: 6.0
Requires PHP: 8.0
Stable tag: 0.5.1
License: Proprietary

A fail-closed, read-only WordPress bridge to the persistent Sustainable Catalyst Research API.

== Purpose ==

The existing /research-hub/ page retains its WordPress/Astra site header, CSS,
editorial preview and original static research records. This plugin adds a
separately labeled, approved-only database-backed Research metadata section
through the already tested Research Workbench adapter.

It does NOT provide private Workspace login, Research writes, WordPress
user-to-Workspace identity mapping, deployment automation, or public access to
original scientific source files. Installing and activating this plugin alone
must not enable backend requests or change live site routing.

== Installation ==

1. Preserve a WordPress database/page backup and a rollback plan.
2. In WordPress Plugins > Add New > Upload Plugin, install the ZIP generated
   by scripts/build_wordpress_bridge.py. Confirm the existing page and theme
   remain intact with the feature disabled.
3. Have the authorized server operator provision a separate, reviewed public
   HTTPS Research API origin on a Sustainable Catalyst subdomain. It must be
   reachable from WordPress; the current host loopback port alone cannot be
   used. The origin must serve only the PUBLIC Research API, never Workspace
   introspection or /api/research/v1/private/*.
4. After database migration, populated backup/restore, public approval/rights
   review, approved host routing and rollback have passed, go to WordPress
   Tools > Research Bridge. Enter the ACTUAL approved public HTTPS Research
   API origin and select "Enable live approved metadata"; save settings.
   No terminal, wp-config editing or research login credentials are needed.
   The form is WordPress administrator-only and protected by a nonce.

   Optionally, operators can override WordPress settings with the existing
   constants in wp-config.php (the hostname below is a placeholder):

   define('SC_RESEARCH_API_ORIGIN', 'https://REPLACE-WITH-APPROVED-HOST.sustainablecatalyst.com');
   define('SC_RESEARCH_BRIDGE_ENABLED', true);

   Do not enable until the real API origin exists and is reviewed. No tokens,
   private source IDs or host IP addresses should be entered in WordPress.
5. In Tools > Research Bridge, refresh to test the actual upstream database.
   Status must read "Connected — verified Research PostgreSQL".
   Also verify HTTPS, GET /wp-json/sc-research/v1/status, /articles, /objects,
   and /graph in an anonymous browser. The status must return
   source=research-postgresql and storage=ready.
6. Confirm backend approved publication changes appear while private drafts
   remain absent; verify persistence after restart, failure handling, caching,
   rights revocation, two-owner isolation and backup/rollback.
7. Test /research-hub/ on desktop/mobile, including the live approved-only
   search and Articles / Research objects filter. The original six editorial records
   must remain labeled editorial preview and never be counted as live backend
   rows. The live backend panel can legitimately show zero approved records.

== Operational safety ==

- Enabled only when the administrator explicitly enables the bridge and
  configures an approved HTTPS Sustainable Catalyst subdomain, either in
  Tools > Research Bridge or via the stronger wp-config.php override.
  New installations start disabled; unverified origin, disabled flag and
  upstream outages cannot be shown as connected.
- Only four fixed GET routes are forwarded; no arbitrary URLs or query input.
- WP request uses wp_safe_remote_get, SSL verification, no cookies, no
  Authorization, no redirects, 6-second timeout and limited JSON size.
- WordPress independently projects allowed public fields; only approved/cleared
  objects and relationships between them are returned.
- Unexpected status, malformed API responses and outages return a 503 without
  exposing backend details or displaying static previews as live data.
- Tools > Research Bridge shows a real read-only backend status check and
  the approved public API address; it never exposes or stores credentials.
- This package does not install or modify reverse proxies, DNS, PHP credentials,
  Workspace sessions, private database connections or production-host services.
- Disabling the flag or deactivating the plugin removes its live integration;
  the existing page and its editor-created static preview remain.

== 0.5.1 runtime uncertainty records in the facade (SC-FIG-05.3), release candidate ==

The figure facade now passes released files with role "runtime-record" (JSON only)
to the Lab renderer. 0.5.0 refused every package that carried one, so bands, fans
and map uncertainty layers could not be shown on the Hub (found in isolated staging
acceptance). Pairing is unchanged: Lab 0.163.1.9 exactly. For isolated staging only.

== 0.5.0 runtime uncertainty (SC-FIG-05.3), release candidate ==

Released figures may draw confidence and prediction bands, ensemble quantile fans
and map uncertainty layers, but only from the Workspace runtime records released
with the views (role "runtime-record"). The Lab renderer refuses any other filled
area. A figure with no record says "Uncertainty unavailable". Requires the Lab
plugin at exactly release 0.163.1.9; bridge 0.5.x and Lab 0.163.1.9 are installed
as a pair. The figure channel rules are unchanged: this release still accepts
only "staging-synthetic". For isolated staging only.

== 0.4.0 geospatial figures (SC-FIG-04) ==

Released figures may include Lab MapLibre map views
(application/vnd.sc.maplibre-view.v1+json): Site Intelligence evidence objects
drawn from inline GeoJSON with layer selection, a scientific legend, time steps,
a side-by-side comparison and the full data table. No basemap, tiles or imagery
are requested. Requires the Lab plugin at exactly release 0.163.1.8 (MapLibre GL
JS 5.24.0, loaded from the Lab plugin under the Research integrity lock; its
worker is checked against SHA-384 and started from a blob: URL, so a page
Content-Security-Policy, if any, must allow worker-src blob:). The figure channel
rules are unchanged: this release still accepts only "staging-synthetic".

== 0.3.0 released scientific figures (SC-FIG-03B) ==

Adds the Research Hub "Released scientific figures" panel. A figure is shown only
while the Research API lists it as released (a live article revision released its
exact, approved sc.figure-bundle.v1). Three more fixed GET routes are forwarded:
/figures/status, /figures/<uuid> and /figures/<uuid>/files/<sha256>. Packages are
re-projected through a field allowlist and file URLs are rewritten to this facade;
file bytes are passed through unchanged as attachment, sandboxed, nosniff. Every
response is Cache-Control: no-store and nothing is cached in WordPress, so an
update, unlisting or withdrawal applies on the next page view.

Figures render with the Lab plugin's own sc.figure-bundle.v1 renderer files,
loaded under the Research integrity lock (Subresource Integrity). There is no
second figure engine in this plugin. Requirements, all fail-closed:

- the bridge is enabled with an approved origin;
- the Sustainable Catalyst Lab plugin is active at exactly release 0.163.1.7;
- a figure channel is set with the SC_RESEARCH_FIGURES_CHANNEL constant or the
  sc_research_figures_channel option. This release accepts only
  "staging-synthetic"; a production channel needs a later, approved release;
- the Research API reports the same channel.

Isolated staging only: SC_RESEARCH_API_STAGING_ORIGIN (wp-config.php) may name a
single-label internal service over plain HTTP, e.g. http://research-web. It is
honoured only when wp_get_environment_type() is local, development or staging,
and only that host is allowed through wp_safe_remote_get. Never set it on
production.

The catalog gains a "Released figures" filter and links to rendered figures; the
graph summary counts released figure nodes.

== 0.2.4 navigation fix ==

Restores nonfunctional original WordPress Research tabs and sidebar navigation
through an external JavaScript asset, rather than relying on the embedded
WordPress editor script. Overview, Discover, Research areas, Knowledge graph,
and Research updates reveal their existing page sections with keyboard support.
Local search/area filtering and structural relationship views use only the six
existing public editorial preview records. External scholarly providers and
private workspace editing remain unavailable, not simulated as live.

== 0.2.3 fix ==

The public BACKEND status indicator now switches from red to green after
verified PostgreSQL connectivity through all four public Research routes.
Only the backend card marker and live status border change color; the site's
red brand decorations, editorial preview counts and private-workspace notices
stay unchanged. Refresh the frontend after upgrading to replace cached CSS/JS.

== 0.2.2 fix ==

A confirmed PostgreSQL-backed connection now updates the legacy preview's
"Not connected / Host integration pending" badge to "Connected — public API"
and the live approved-article count. This is an additive status correction:
all six static editorial entries, preview counts and separate private Workspace
notices remain unchanged. Status is updated only after verification of the
approved public facade endpoints and persistent PostgreSQL source.

== 0.2.1 fix ==

The browser adapter now targets WordPress REST facade paths /status, /articles,
/objects and /graph instead of forwarding the upstream-only /public/status
and /graph/metadata path names. Existing gateway security and the disabled-
by-default setting are unchanged. Update the installed plugin to v0.2.1 and
verify all four anonymous WordPress REST routes plus the browser live panel.

== Support/maintenance ==

Research repo: Content-Catalyst-LLC/sustainable-catalyst-research
Build: RH-PROD-06. This source-only candidate is not evidence of production
deployment until live WordPress acceptance is recorded.
