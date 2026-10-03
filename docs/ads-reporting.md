# Advertising reports

Meta Ads, Google Ads and TikTok Ads are separate reporting-only connections. Connect in Connections, choose the discovered account in Analytics, then collect a saved report. Organization-scoped history, last successful snapshot, JSON export and agent `report.refresh` reuse the existing account-report service. An account report never creates an ad, changes a campaign, adjusts a budget or starts spending. Advertising accounts cannot be campaign destinations.

All three use the latest 30 completed **account-local** dates, explicit currency/timezone, native impressions/clicks and spend. Native integer/decimal values stay strings in report tables to preserve precision. Missing rows/counters are unavailable rather than zero. Conversion attribution, cross-currency totals and sums of unique audience figures are not inferred. Recent provider reports can change. Partial row coverage is displayed with the snapshot.

## Google Ads

Configure existing `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` and separate `GOOGLE_ADS_DEVELOPER_TOKEN`. Enable Google Ads API and request the developer-token access level needed for intended accounts. The Google scope `https://www.googleapis.com/auth/adwords` permits more operations natively than this adapter exposes: Post Chief only uses `listAccessibleCustomers` and fixed `googleAds:search` queries against v25. No owner/agent-supplied GAQL is accepted.

Register `/api/connections/oauth/google_ads/callback` on the stable public origin. Confidential offline consent stores encrypted renewable grants through the serialized refresh service. Discovery handles at most 100 directly accessible customers and 100 enabled non-manager clients per manager, at most 100 unique connected clients. Each connection retains the verified manager login ID where required. Discovery fails visibly beyond bounds. Daily account reports and campaign window totals are separate tables; campaign totals are bounded at 1,000 rows with truncation indicated. Native cost is integer `cost_micros` in account currency. A manager is an authorization context, not a reportable client.

## Meta Ads

Use existing Meta app credentials and a separate `/api/connections/oauth/meta_ads/callback`. Request only `ads_read`, verified through the granted permissions edge. Read account access, identity/currency/timezone and synchronous insights through fixed Graph GET endpoints. Discovery is bounded at 100 accounts; paginated campaign insights at 1,000 rows. Fixed-origin cursor pagination never follows provider URLs that may carry tokens.

This connection uses a long-lived **user** grant, not the Facebook Page token used for organic publishing. Its returned expiry is recorded. Reconnect at expiry or revocation; an automatic recurring renewal is not claimed. Access review requirements depend on app permissions and users/assets; do not claim approval before the final live pass. Clicks are native all-ad clicks, not link clicks. No actions/conversion attribution fields requested.

## TikTok Ads

Configure separate `TIKTOK_ADS_APP_ID`, `TIKTOK_ADS_SECRET`, and the **app-generated advertiser authorization URL** as `TIKTOK_ADS_AUTHORIZATION_URL`. Its app ID and exact `/api/connections/oauth/tiktok_ads/callback` must match configuration; the owner-bound single-use state is injected. This is distinct from Login Kit and account-holder authorization for TikTok business. Only advertiser information and Reporting permissions are needed in the app; do not request ad-management write capabilities for this connector.

The current advertiser API returns a long-term access token; the deprecated advertiser refresh endpoint is not used. Tokens are encrypted, native revocation errors require reconnection. Discovery uses the authorized advertiser list, max 100. Reports re-read identity/currency/timezone, require a recognized IANA timezone, and query BASIC AUCTION campaign/day impressions, clicks and spend. Report pagination is bounded at ten 100-row pages; continuation is explicitly reported. Reservation/specialized products remain outside this report. Censored values such as `<5` remain text, never zero.

## Final acceptance

After stable hosting/OAuth setup, connect each app, confirm intended account IDs, currency/timezone, provider permissions and access level. Compare a known report to the provider dashboard for the same dates, inspect saved snapshot/export/history and errors, then verify disconnection/revocation. No live advertising requests or writes are authorized merely by implementing these adapters.

Sources: [Google Ads headers](https://developers.google.com/google-ads/api/rest/auth), [Google Ads search](https://developers.google.com/google-ads/api/rest/common/search), [Meta official Marketing SDK](https://github.com/facebook/facebook-python-business-sdk), [TikTok advertiser authorization](https://business-api.tiktok.com/portal/docs?id=1738373141733378), [TikTok official SDK](https://github.com/tiktok/tiktok-business-api-sdk). Current TikTok portal documentation takes precedence over older generated SDK prose describing creator refresh tokens.
