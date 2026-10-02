# Native providers

## Bluesky (Gate 4)

Connect with a handle and an App Password using the Connections API. Post Chief stores encrypted session and refresh tokens, not the password. Reconnecting preserves the account ID and publication history. This release supports Bluesky-hosted PDS domains (`bsky.social` and subdomains of `bsky.network`); other federated PDS hosts require an explicit future connection policy.

Text uses the [post lexicon](https://github.com/bluesky-social/atproto/blob/main/lexicons/app/bsky/feed/post.json): at most 300 grapheme clusters and 3000 UTF-8 bytes. Links use UTF-8 byte offsets in facets. Images use the [image embed lexicon](https://github.com/bluesky-social/atproto/blob/main/lexicons/app/bsky/embed/images.json): at most four raster images, each at most 2,000,000 bytes. SVG must be rasterized before publishing.

MP4 videos are uploaded through the video service with a PDS-scoped service token. The adapter returns a pending state while the [video job](https://github.com/bluesky-social/atproto/blob/main/lexicons/app/bsky/video/defs.json) processes; the scheduler persists and resumes that job rather than reuploading. Post Chief's application upload limit is 80 MiB, lower than the provider's current maximum.

Each publication gets a stable repository record key in the [TID syntax required by feed posts](https://atproto.com/specs/record-key). It encodes a digest of the publication ID; actual publication time is stored in `createdAt`. Before writing, the adapter checks that key; an existing record is reconciled as the same publication. Writes use compare-and-swap creation through [putRecord](https://github.com/bluesky-social/atproto/blob/main/lexicons/com/atproto/repo/putRecord.json), preserving existing posts. Session refresh updates the encrypted credentials through the calling publication worker.

Analytics reports supported engagement counters. Missing counters and unsupported impressions are not invented. Comments and replies are not advertised in this MVP adapter.

Live connection and publication still require the owner's Bluesky account and intended content. Local HTTP contract tests verify requests and recovery behavior; they do not prove live provider access.
# Meta connections

Configure `META_CLIENT_ID`, `META_CLIENT_SECRET`, and `META_API_VERSION` for a Facebook App using Facebook Login. The owner authorizes Pages; linked professional Instagram accounts are discovered from those Pages. Consumer Instagram accounts are unsupported. Configure a separate Threads App using `THREADS_CLIENT_ID` and `THREADS_CLIENT_SECRET`.

Register exact callbacks at `PUBLIC_URL/api/connections/oauth/meta/callback` and `PUBLIC_URL/api/connections/oauth/threads/callback`. Start with an authenticated, CSRF-protected POST to `/api/connections/oauth/{provider}/authorize`, then open the returned URL. State expires after ten minutes, is bound to the owner and organization, and is atomically consumed before exchange. Tokens are encrypted and never returned to the browser. App review and the required permissions must be available for accounts outside app roles.

Facebook supports text, raster photos and MP4 Reels; Instagram supports JPEG images, MP4 Reels and carousels (application cap: ten items); Threads supports text, images, video and carousels (twenty items). Application media size is capped at 80 MiB. Media URLs for Instagram, Threads and hosted Facebook Reels require public HTTPS. Platform encoding, dimensions, duration and permission checks can still reject media. File acceptance alone does not prove platform eligibility.

Provider preparation returns persisted checkpoints. Public mutations require a saved `publish_intent` before execution. These providers have no application idempotency key: an uncertain response or recovery at that intent requires reconciliation. Do not blindly retry and risk duplicate posts. Facebook Reels wait for the publishing phase to complete. Instagram and Threads reuse stored containers and poll processing status. Threads long-lived tokens support refresh while unexpired; revoked or expired connections require owner reconnection.

Implementation follows Meta's official [Facebook collection](https://www.postman.com/meta/facebook/documentation/r56bjfd/facebook-api), [Instagram collection](https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api), and [Threads collection](https://www.postman.com/meta/threads/documentation/dht3nzz/threads-api). Live account acceptance remains a separate gate check requiring the owner's configured apps and consent.
