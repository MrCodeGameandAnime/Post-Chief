# Native providers

## Bluesky (Gate 4)

Connect with a handle and an App Password using the Connections API. Post Chief stores encrypted session and refresh tokens, not the password. Reconnecting preserves the account ID and publication history. This release supports Bluesky-hosted PDS domains (`bsky.social` and subdomains of `bsky.network`); other federated PDS hosts require an explicit future connection policy.

Text uses the [post lexicon](https://github.com/bluesky-social/atproto/blob/main/lexicons/app/bsky/feed/post.json): at most 300 grapheme clusters and 3000 UTF-8 bytes. Links use UTF-8 byte offsets in facets. Images use the [image embed lexicon](https://github.com/bluesky-social/atproto/blob/main/lexicons/app/bsky/embed/images.json): at most four raster images, each at most 2,000,000 bytes. SVG must be rasterized before publishing.

MP4 videos are uploaded through the video service with a PDS-scoped service token. The adapter returns a pending state while the [video job](https://github.com/bluesky-social/atproto/blob/main/lexicons/app/bsky/video/defs.json) processes; the scheduler persists and resumes that job rather than reuploading. Post Chief's application upload limit is 80 MiB, lower than the provider's current maximum.

Each publication gets a stable repository record key in the [TID syntax required by feed posts](https://atproto.com/specs/record-key). It encodes a digest of the publication ID; actual publication time is stored in `createdAt`. Before writing, the adapter checks that key; an existing record is reconciled as the same publication. Writes use compare-and-swap creation through [putRecord](https://github.com/bluesky-social/atproto/blob/main/lexicons/com/atproto/repo/putRecord.json), preserving existing posts. Session refresh updates the encrypted credentials through the calling publication worker.

Analytics reports supported engagement counters. Missing counters and unsupported impressions are not invented. Comments and replies are not advertised in this MVP adapter.

Live connection and publication still require the owner's Bluesky account and intended content. Local HTTP contract tests verify requests and recovery behavior; they do not prove live provider access.

## Pinterest (expanded Gate 13)

Configure `PINTEREST_CLIENT_ID` and `PINTEREST_CLIENT_SECRET`; register the exact callback `PUBLIC_URL/api/connections/oauth/pinterest/callback`. Consent requests `boards:read,pins:read,pins:write,user_accounts:read`. Encrypted continuous refresh tokens use a durable renewal intent shared by publishing, analytics and board discovery. Unconfirmed renewal requires reconnecting rather than reusing a possibly rotated token.

After connecting, choose an accessible public board in Connections. Discovery is paginated. Switching an already selected board is blocked while pending drafts/deliveries exist; an initially missing board can be configured to recover setup errors. Reconnecting requires choosing the board again. A publication retains its board ID in its encrypted public-write intent.

The initial adapter publishes one JPEG/PNG image up to the 10 MB application cap, with a description up to 800 characters and asset alt text up to 500. Images are sent as Base64. Video, carousel, board creation, custom Pin titles/links and native Pin edits are not implemented in this first pass. Text-only campaigns are rejected for Pinterest. Scheduling, independent outcomes, permalinks, reconciliation and reviewed feedback use the existing paths.

Pin analytics cover the last 30 UTC days. Impressions and outbound clicks preserve that window; Pin clicks and saves remain in provider data. Missing counters remain unavailable. App access, permissions, consent and intended live Pin acceptance remain for the final owner pass. Sources: [Pinterest OpenAPI](https://github.com/pinterest/api-description) and [authorization guide](https://developers.pinterest.com/docs/getting-started/set-up-authentication-and-authorization/).

## X (core foundation)

### Handoff without API credits

Saved campaigns expose **X · manual handoff** under Delivery even without X credentials. Copy the platform-specific caption, download selected JPEG/PNG/WebP images, post in X and record the URL and actual publication time. The record is explicitly owner-reported, is never dispatched by workers and has no native analytics collection. Existing automated publication outcomes are unchanged. The record and unavailable metrics flow into the same reviewed GitHub feedback. One campaign cannot use both direct and manual X delivery. See [final-pass instructions and limitations](mvp-final-pass.md).

### Direct API integration


The owner's core networks are Facebook, Instagram, Threads and X. Bluesky and LinkedIn remain optional follow-ups. X live account acceptance has not been performed.

Configure `X_CLIENT_ID` and `X_CLIENT_SECRET` with the **OAuth 2.0** credentials for a confidential Web App in the X Developer Console. These are not the API key/secret or an app-only bearer token. Enable user authentication and register the exact callback `PUBLIC_URL/api/connections/oauth/x/callback`, plus the dashboard website URL. Start consent with **Connect X** in Connections. Requested scopes are `tweet.read tweet.write users.read media.write offline.access`. State is owner/organization-bound, expires after ten minutes and is single-use. PKCE uses S256 with a server-keyed verifier derived for each random state; token exchange uses HTTP Basic client authentication. Tokens stay encrypted on the server.

X API requests use paid credits. Review credits and a spending limit in the Developer Console before live use; Post Chief does not purchase credits or change billing. X analytics collection is **manual** through Request fresh metrics: successful or non-transient failed reads stop until another request. Transient failures retain the normal retry deadline. Public metrics include reported likes, replies, reposts and impressions when available; missing fields remain unavailable. Reposts retain their meaning in provider metrics.

This adapter supports ordinary posts with text and up to four JPEG, PNG or WebP images, each at most 5 MB. Text validation uses a conservative weighted bound of 280: long URLs and complex emoji can be overcounted. A provider override can shorten X copy without changing other destinations. Videos, GIFs, long posts, replies, quote posts, native text edits and image alt-text metadata are not implemented in this first pass. Each image ID and expiry is persisted before publication intent. Expired uploads require review; upload acceptance does not establish public publication.

X posts use the existing scheduler, independent destination outcomes and owner reconciliation. A lost public-write response requires checking X before retrying. Successful posts retain the returned numeric ID and a public status link for feedback. Rotating refresh grants use a durable encrypted account intent and conditional save before other provider work. Concurrent jobs wait through retry; interrupted or unconfirmed renewal requires reconnecting, rather than reusing a possibly consumed refresh token. Successful renewed credentials are stored before media or public I/O.

Official contracts: [OAuth PKCE](https://docs.x.com/fundamentals/authentication/oauth-2-0/user-access-token), [create posts](https://docs.x.com/x-api/posts/create-post), [media upload](https://docs.x.com/x-api/media/upload-media), [post lookup](https://docs.x.com/x-api/posts/get-post-by-id), [pricing](https://docs.x.com/x-api/getting-started/pricing).

## LinkedIn (Gate 6)

Configure `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET`, and `LINKEDIN_API_VERSION` (default `202609`). Register `PUBLIC_URL/api/connections/oauth/linkedin/callback`. Enable the OpenID Connect and Share on LinkedIn products. The default `LINKEDIN_SCOPES` is `openid profile w_member_social`; email is unnecessary. Start the same owner OAuth flow with provider `linkedin`. Identity comes from authenticated userinfo, never an unverified ID token. Tokens, optional programmatic refresh tokens and resumable video upload credentials are encrypted.

Text, JPEG/PNG/GIF, organic multi-image posts (up to twenty images), and multipart MP4 video uploads use native Posts, Images and Videos APIs. The application caps media at 80 MiB. `x-restli-id` is retained as the publication URN. Public writes use a persisted intent; uncertain results require reconciliation. `w_member_social` alone cannot GET versioned image status: upload checkpoints provide processing time, and provider acceptance is still required. Platform media constraints may reject an otherwise valid application asset.

Company Pages require approved organization products/scopes. Add permitted `w_organization_social`, organization-read/admin permissions and `r_organization_social` to `LINKEDIN_SCOPES`, then reconnect. Use `/api/connections/linkedin/{member-account-id}/organizations` to list and POST an eligible Page. Selection checks the authenticated member's approved publishing role against LinkedIn; arbitrary organization URNs are rejected.

Member analytics separately requires approved `r_member_postAnalytics`; organization analytics requires `r_organization_social`. Missing access is reported explicitly and does not become zero-valued metrics. Programmatic refresh is available only when LinkedIn issues a refresh token; other connections require reauthorization after expiration.

Contracts: [Posts](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api), [Images](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/images-api), [Videos](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/videos-api), and [member analytics](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/members/post-statistics).

## Meta connections (Gate 5)

Configure `META_CLIENT_ID`, `META_CLIENT_SECRET`, and `META_API_VERSION` for a Facebook App using Facebook Login. The owner authorizes Pages; linked professional Instagram accounts are discovered from those Pages. Consumer Instagram accounts are unsupported. Threads requires the Access the Threads API use case and its own **Threads App ID/secret**, configured as `THREADS_CLIENT_ID` and `THREADS_CLIENT_SECRET`; Facebook Login credentials are not interchangeable. A separate developer app registration is recommended for this setup. If Meta offers the Threads use case on the existing registration, its Threads-specific credentials can also be used: Post Chief does not require the two registrations to be separate. Both connections use the same Post Chief dashboard.

Register exact callbacks at `PUBLIC_URL/api/connections/oauth/meta/callback` and `PUBLIC_URL/api/connections/oauth/threads/callback`. Start with an authenticated, CSRF-protected POST to `/api/connections/oauth/{provider}/authorize`, then open the returned URL. State expires after ten minutes, is bound to the owner and organization, and is atomically consumed before exchange. Tokens are encrypted and never returned to the browser. App review and the required permissions must be available for accounts outside app roles.

The Threads connection requests `threads_basic`, `threads_content_publish` and `threads_manage_insights` for profile/post access, publishing and analytics. Enable those permissions in the Threads use case before connecting. Use the Threads-specific callback above; the Facebook/Instagram callback does not handle Threads consent.

Threads callback failures identify whether the authorization-code exchange, long-lived token exchange or profile lookup failed, with numeric provider codes when available. Provider response text and tokens are not displayed. The normalized `AUTH_REVOKED` reason does not by itself establish that the owner revoked access. Start each retry from Connections: callback state is single-use even when an exchange fails, and refreshing an old callback cannot retry consent.

The long-lived Threads exchange sends the short token as the `access_token` query parameter, matching [Meta's official sample](https://github.com/fbsamples/threads_api/blob/main/src/index.js). Profile requests continue to use bearer authentication. Keep exchange request URLs out of logs because they contain credentials.

Both the Facebook and Instagram dashboard buttons currently use the combined Meta consent flow. Its requested scopes are `business_management`, `pages_show_list`, `pages_read_engagement`, `pages_read_user_content`, `pages_manage_posts`, `instagram_basic`, `instagram_content_publish` and `instagram_manage_insights`. The Instagram setup's required-content button alone may not enable Page publishing or Instagram insights. In the Instagram use case, open Permissions and features and add `instagram_manage_insights`. Add the Content management use case **Manage everything on your Page**, customize it, and enable `pages_manage_posts` in its permission table before connecting. An `Invalid Scopes` error naming `pages_manage_posts` or `instagram_manage_insights` means the app configuration must be checked for those permissions; retrying the same authorization URL will not add them. Register the exact tunnel hostname under App Domains too, and update it and the redirect URI if the Quick Tunnel hostname changes.

Facebook reactions and comments summaries also require `pages_read_user_content`; add it in the Page use case permission table and start fresh consent after enabling it. Existing publication records are retained on reconnection; reauthorization does not republish campaigns.

Meta configuration failures can redirect back with `error_code` and no state. Post Chief returns a fixed diagnostic and never exchanges a code or creates a connection for such a callback. Successful callbacks still require valid, single-use actor-bound state. Start a fresh connection from Connections after correcting the provider configuration.

Facebook supports text, raster photos and MP4 Reels; Instagram supports JPEG images, MP4 Reels and carousels (application cap: ten items); Threads supports text, images, video and carousels (twenty items). Application media size is capped at 80 MiB. Media URLs for Instagram, Threads and hosted Facebook Reels require public HTTPS. Platform encoding, dimensions, duration and permission checks can still reject media. File acceptance alone does not prove platform eligibility.

Provider preparation returns persisted checkpoints. Public mutations require a saved `publish_intent` before execution. These providers have no application idempotency key: an uncertain response or recovery at that intent requires reconciliation. Do not blindly retry and risk duplicate posts. Facebook Reels wait for the publishing phase to complete. Instagram and Threads reuse stored containers and poll processing status. Threads long-lived tokens support refresh while unexpired; revoked or expired connections require owner reconnection.

Implementation follows Meta's official [Facebook collection](https://www.postman.com/meta/facebook/documentation/r56bjfd/facebook-api), [Instagram collection](https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api), and [Threads collection](https://www.postman.com/meta/threads/documentation/dht3nzz/threads-api). Live account acceptance remains a separate gate check requiring the owner's configured apps and consent.

### Maintaining an existing campaign

After delivery, owners can add another connected destination without resetting existing publications. The new destination stays paused until explicitly scheduled or published. Its provider copy and media can be edited while paused and unattempted. Shared copy for an existing provider cannot be changed through this action. Existing publication IDs, timestamps, attempts, analytics and original master copy are retained. These maintenance endpoints currently require an owner session.

Published Facebook feed post text can be updated using **Edit Facebook text**. Load current native text, change it, review before/after, then apply that exact review. Reviews expire after ten minutes, are owner/publication-bound and single-use, and reject a changed remote caption. Post media is retained. Edit intent and result are recorded separately in the publication's feedback history. Unconfirmed edits require loading and verifying current native text before another edit; they are never retried automatically. Facebook has no conditional text-update API, so a native edit between the final read and update can still conflict.

Meta's [Facebook Post SDK](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/post.py) exposes the `message` update parameter. Its [Instagram Media SDK](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/igmedia.py) does not expose caption updates. Instagram captions must therefore be changed through the **Edit on Instagram** link. Changing published media or editing other post formats is not implemented; this flow never deletes and recreates a post.
