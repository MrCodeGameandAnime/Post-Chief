# Native providers

## Bluesky (Gate 4)

Connect with a handle and an App Password using the Connections API. Post Chief stores encrypted session and refresh tokens, not the password. Reconnecting preserves the account ID and publication history. This release supports Bluesky-hosted PDS domains (`bsky.social` and subdomains of `bsky.network`); other federated PDS hosts require an explicit future connection policy.

Text uses the [post lexicon](https://github.com/bluesky-social/atproto/blob/main/lexicons/app/bsky/feed/post.json): at most 300 grapheme clusters and 3000 UTF-8 bytes. Links use UTF-8 byte offsets in facets. Images use the [image embed lexicon](https://github.com/bluesky-social/atproto/blob/main/lexicons/app/bsky/embed/images.json): at most four raster images, each at most 2,000,000 bytes. SVG must be rasterized before publishing.

MP4 videos are uploaded through the video service with a PDS-scoped service token. The adapter returns a pending state while the [video job](https://github.com/bluesky-social/atproto/blob/main/lexicons/app/bsky/video/defs.json) processes; the scheduler persists and resumes that job rather than reuploading. Post Chief's application upload limit is 80 MiB, lower than the provider's current maximum.

Each publication gets a stable repository record key in the [TID syntax required by feed posts](https://atproto.com/specs/record-key). It encodes a digest of the publication ID; actual publication time is stored in `createdAt`. Before writing, the adapter checks that key; an existing record is reconciled as the same publication. Writes use compare-and-swap creation through [putRecord](https://github.com/bluesky-social/atproto/blob/main/lexicons/com/atproto/repo/putRecord.json), preserving existing posts. Session refresh updates the encrypted credentials through the calling publication worker.

Analytics reports supported engagement counters. Missing counters and unsupported impressions are not invented. Comments and replies are not advertised in this MVP adapter.

Live connection and publication still require the owner's Bluesky account and intended content. Local HTTP contract tests verify requests and recovery behavior; they do not prove live provider access.
