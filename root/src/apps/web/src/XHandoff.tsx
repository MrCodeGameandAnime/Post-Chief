import { useState, type FormEvent } from "react";
import { send } from "../../../packages/api-client/src";
import type { Asset, Campaign } from "../../../packages/shared-types/src";

type Run = (action: () => Promise<unknown>) => Promise<boolean>;
const localNow = () => {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60000)
    .toISOString().slice(0, 16);
};

export function XHandoff({ campaign, assets, run, busy, stale, close }: {
  campaign: Campaign; assets: Asset[]; run: Run; busy: boolean;
  stale: boolean; close: () => void;
}) {
  const [body, setBody] = useState(campaign.overrides.x?.body ?? campaign.body);
  const [media, setMedia] = useState(campaign.overrides.x?.asset_ids ?? campaign.asset_ids);
  const [url, setUrl] = useState("");
  const [publishedAt, setPublishedAt] = useState(localNow);
  const [confirmed, setConfirmed] = useState(false);
  const [copyMessage, setCopyMessage] = useState("");
  const existing = campaign.external_posts?.find((post) => post.provider === "x");
  if (campaign.publications.some((post) => post.provider === "x")) return null;
  if (existing) return (
    <article className="publication">
      <strong>X · manual handoff</strong>
      <p>Published · reported by you</p>
      <p className="muted">{new Date(existing.published_at).toLocaleString()}</p>
      <a href={existing.url} target="_blank" rel="noreferrer">View X post ↗</a>
      <details><summary>Recorded copy</summary><p className="post-text">{existing.body}</p></details>
      <small>Post Chief did not publish or verify this post. Native metrics are unavailable.</small>
    </article>
  );
  const selected = media.map((id) => assets.find((asset) => asset.id === id));
  const unsupported = selected.some((asset) => !asset ||
    !["image/jpeg", "image/png", "image/webp"].includes(asset.mime_type) || asset.byte_size > 5_000_000);
  const validMedia = !unsupported && media.length <= 4;
  const record = async (event: FormEvent) => {
    event.preventDefault();
    const timestamp = new Date(publishedAt);
    if (Number.isNaN(timestamp.getTime())) return;
    if (await run(() => send("/campaigns/" + campaign.id + "/external/x", {
      revision: campaign.revision, body, asset_ids: media, url,
      published_at: timestamp.toISOString(), confirmed_published: confirmed,
    }))) close();
  };
  return (
    <details className="publication">
      <summary>X · manual handoff</summary>
      <p>Post in X yourself, then record its URL here. No API connection or credits are needed. This handoff is not automatically scheduled.</p>
      <form onSubmit={record}>
        <label>Copy for X<textarea rows={5} value={body} maxLength={20000}
          onChange={(event) => { setBody(event.target.value); setCopyMessage(""); }} /></label>
        <p className="muted">Review length in X before posting. Long posts depend on your X account.</p>
        <button type="button" onClick={async () => {
          try { await navigator.clipboard.writeText(body); setCopyMessage("Caption copied."); }
          catch { setCopyMessage("Select the caption above and copy it manually."); }
        }}>Copy caption</button>
        <p role="status">{copyMessage}</p>
        <fieldset><legend>Images for X</legend>
          {assets.filter((asset) => media.includes(asset.id) ||
            (["image/jpeg", "image/png", "image/webp"].includes(asset.mime_type) && asset.byte_size <= 5_000_000))
            .map((asset) => <div key={asset.id}>
              <label className="check"><input type="checkbox" checked={media.includes(asset.id)}
                onChange={() => setMedia(media.includes(asset.id) ? media.filter((id) => id !== asset.id) : [...media, asset.id])} />{asset.name}</label>
              {media.includes(asset.id) && <a href={asset.file_url} download={asset.name}>Download {asset.name}</a>}
            </div>)}
          {!validMedia && <p role="alert">Choose at most four JPEG, PNG or WebP images, 5 MB each.</p>}
        </fieldset>
        <a href="https://x.com/compose/post" target="_blank" rel="noreferrer">Open X composer ↗</a>
        <p className="muted">Attach the downloaded images and publish in X. Keep this page open until you record the result; unsaved handoff changes are temporary.</p>
        <label>Published X post URL<input type="url" required value={url}
          placeholder="https://x.com/your_account/status/…"
          onChange={(event) => setUrl(event.target.value)} /></label>
        <label>Published at, in your timezone<input type="datetime-local" required
          value={publishedAt} onChange={(event) => setPublishedAt(event.target.value)} /></label>
        <label className="check"><input type="checkbox" required checked={confirmed}
          onChange={(event) => setConfirmed(event.target.checked)} />I published this copy and media in X.</label>
        <button disabled={busy || stale || !confirmed || !validMedia || (!body.trim() && !media.length)}>Record published URL</button>
        {stale && <p>Reopen this campaign to load its latest revision before recording.</p>}
      </form>
    </details>
  );
}
