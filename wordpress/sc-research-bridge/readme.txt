=== Sustainable Catalyst Research Bridge ===
Contributors: sustainable-catalyst
Tags: research, public metadata, backend, research publications
Requires at least: 6.0
Requires PHP: 8.0
Stable tag: 0.2.4
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
   reachable from WordPress; the current Contabo loopback port alone cannot be
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
   private source IDs or Contabo IP addresses should be entered in WordPress.
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
  Workspace sessions, private database connections or Contabo services.
- Disabling the flag or deactivating the plugin removes its live integration;
  the existing page and its editor-created static preview remain.

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
"Not connected / Contabo integration pending" badge to "Connected — public API"
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
