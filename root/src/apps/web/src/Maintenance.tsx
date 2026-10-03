import { useState } from "react";
import { request, send } from "../../../packages/api-client/src";
import { YouTubeOptions } from "./YouTubeOptions";
import type { YouTubeOptions as VideoOptions } from "../../../packages/shared-types/src";
import type {
  Campaign,
  Connection,
  Asset,
  Publication,
} from "../../../packages/shared-types/src";

type Run = (action: () => Promise<unknown>) => Promise<boolean>;
type Actions = { run: Run; busy: boolean; close: () => void };

export function AddDestination({
  campaign,
  accounts,
  assets,
  run,
  busy,
  close,
}: Actions & {
  campaign: Campaign;
  accounts: Connection[];
  assets: Asset[];
}) {
  const available = accounts.filter(
    (a) =>
      a.active && !campaign.publications.some((p) => p.account_id === a.id) &&
      !(a.provider === "x" && campaign.external_posts?.length),
  );
  const [accountId, setAccountId] = useState("");
  const [body, setBody] = useState(campaign.body);
  const [youtube, setYouTube] = useState<VideoOptions | undefined>(campaign.overrides.youtube?.youtube);
  const account = available.find((a) => a.id === accountId);
  if (!available.length) return null;
  const media = account
    ? (campaign.overrides[account.provider]?.asset_ids ?? campaign.asset_ids)
    : campaign.asset_ids;
  return (
    <article className="panel">
      <h3>Add a destination</h3>
      <p>
        Add this campaign to another account. It stays paused until you choose
        delivery.
      </p>
      <label>
        New destination
        <select
          value={accountId}
          onChange={(e) => {
            setAccountId(e.target.value);
            const selected = available.find((a) => a.id === e.target.value);
            setBody(
              selected
                ? (campaign.overrides[selected.provider]?.body ?? campaign.body)
                : campaign.body,
            );
          }}
        >
          <option value="">Choose an account</option>
          {available.map((a) => (
            <option key={a.id} value={a.id}>
              {a.provider} · {a.name}
            </option>
          ))}
        </select>
      </label>
      <label>
        Copy for the new destination
        <textarea
          rows={6}
          maxLength={20000}
          value={body}
          onChange={(e) => setBody(e.target.value)}
        />
      </label>
      <p>
        Media:{" "}
        {media
          .map((id) => assets.find((a) => a.id === id)?.name ?? id)
          .join(", ") || "Text only"}
      </p>
      {account?.provider === "youtube" && <YouTubeOptions title={campaign.title} value={youtube} change={setYouTube} />}
      <button
        disabled={busy || !account}
        onClick={async () => {
          if (
            await run(() =>
              send("/campaigns/" + campaign.id + "/destinations", {
                revision: campaign.revision,
                account_id: accountId,
                body,
                ...(account?.provider === "youtube" ? { youtube: youtube ?? { privacy_status: "private" } } : {}),
              }),
            )
          )
            close();
        }}
      >
        Save new destination
      </button>
    </article>
  );
}

export function DestinationDelivery({
  campaign,
  publication,
  assets,
  run,
  busy,
  close,
}: Actions & {
  campaign: Campaign;
  publication: Publication;
  assets: Asset[];
}) {
  const [at, setAt] = useState("");
  const [youtube, setYouTube] = useState<VideoOptions | undefined>(campaign.overrides.youtube?.youtube);
  const [body, setBody] = useState(
    campaign.overrides[publication.provider]?.body ?? campaign.body,
  );
  const [media, setMedia] = useState(
    campaign.overrides[publication.provider]?.asset_ids ?? campaign.asset_ids,
  );
  const savedBody =
    campaign.overrides[publication.provider]?.body ?? campaign.body;
  const savedMedia =
    campaign.overrides[publication.provider]?.asset_ids ?? campaign.asset_ids;
  const dirty =
    body !== savedBody || JSON.stringify(media) !== JSON.stringify(savedMedia) ||
    (publication.provider === "youtube" && JSON.stringify(youtube) !== JSON.stringify(campaign.overrides.youtube?.youtube));
  const deliver = async (scheduled: boolean) => {
    if (
      await run(() =>
        send("/publications/" + publication.id + "/deliver", {
          revision: campaign.revision,
          ...(scheduled ? { scheduled_at: new Date(at).toISOString() } : {}),
        }),
      )
    )
      close();
  };
  return (
    <div>
      <p>This destination is paused. Choose when to deliver it.</p>
      <details>
        <summary>Edit this destination's draft</summary>
        {publication.provider === "youtube" && <YouTubeOptions title={campaign.title} value={youtube} change={setYouTube} />}
        <label>
          Copy
          <textarea
            rows={6}
            maxLength={20000}
            value={body}
            onChange={(e) => setBody(e.target.value)}
          />
        </label>
        <fieldset>
          <legend>Media for this destination</legend>
          {assets.map((a) => (
            <label className="check" key={a.id}>
              <input
                type="checkbox"
                checked={media.includes(a.id)}
                onChange={(e) =>
                  setMedia(
                    e.target.checked
                      ? [...media, a.id]
                      : media.filter((id) => id !== a.id),
                  )
                }
              />
              {a.name}
            </label>
          ))}
        </fieldset>
        <button
          disabled={busy || !dirty}
          onClick={async () => {
            if (
              await run(() =>
                send(
                  "/publications/" + publication.id + "/draft",
                  {
                    revision: campaign.revision,
                    account_id: publication.account_id,
                    body,
                    asset_ids: media,
                    ...(publication.provider === "youtube" ? { youtube: youtube ?? { privacy_status: "private" } } : {}),
                  },
                  "PATCH",
                ),
              )
            )
              close();
          }}
        >
          Save destination draft
        </button>
      </details>
      <label>
        Schedule this destination
        <input
          type="datetime-local"
          value={at}
          onChange={(e) => setAt(e.target.value)}
        />
      </label>
      <div className="toolbar">
        <button disabled={busy || !at || dirty} onClick={() => deliver(true)}>
          Schedule {publication.provider} only
        </button>
        <button disabled={busy || dirty} onClick={() => deliver(false)}>
          Publish to {publication.provider} only
        </button>
      </div>
      {dirty && <p>Save your destination copy and media before delivery.</p>}
    </div>
  );
}

export function FacebookTextEdit({
  publicationId,
  run,
  busy,
}: {
  publicationId: string;
  run: Run;
  busy: boolean;
}) {
  const [loaded, setLoaded] = useState(false);
  const [body, setBody] = useState("");
  const [currentBody, setCurrentBody] = useState("");
  const [needsVerification, setNeedsVerification] = useState(false);
  const [review, setReview] = useState<{
    before: string;
    after: string;
    review_token: string;
  } | null>(null);
  const [updated, setUpdated] = useState(false);
  const path = "/publications/" + publicationId + "/text";
  const load = () =>
    run(async () => {
      const live = await request<{ body: string; needs_verification: boolean }>(
        path,
      );
      setBody(live.body);
      setCurrentBody(live.body);
      setNeedsVerification(live.needs_verification);
      setLoaded(true);
      setReview(null);
      setUpdated(false);
    });
  return (
    <details>
      <summary>Edit Facebook text</summary>
      <p>
        Updates this existing post's text. Its image and publication history are
        retained.
      </p>
      <button disabled={busy} onClick={load}>
        Load current Facebook text
      </button>
      {loaded && (
        <>
          {needsVerification ? (
            <div>
              <p>
                An earlier edit was not confirmed. Current text on Facebook:
              </p>
              <pre className="post-text">{currentBody}</pre>
              <button
                disabled={busy}
                onClick={async () => {
                  if (
                    await run(() =>
                      send(path + "/verify", { body: currentBody }),
                    )
                  )
                    setNeedsVerification(false);
                }}
              >
                Confirm this current text and resolve earlier edit
              </button>
            </div>
          ) : (
            <>
              <label>
                Updated Facebook text
                <textarea
                  rows={6}
                  maxLength={20000}
                  value={body}
                  onChange={(e) => {
                    setBody(e.target.value);
                    setReview(null);
                    setUpdated(false);
                  }}
                />
              </label>
              <button
                disabled={busy || body === currentBody}
                onClick={() =>
                  run(async () => {
                    setReview(await send(path + "/preview", { body }));
                  })
                }
              >
                Review text change
              </button>
            </>
          )}
          {review && (
            <div>
              <h4>Current text</h4>
              <pre className="post-text">{review.before}</pre>
              <h4>New text</h4>
              <pre className="post-text">{review.after}</pre>
              <button
                className="primary"
                disabled={busy}
                onClick={async () => {
                  if (
                    await run(() =>
                      send(path + "/apply", {
                        body: review.after,
                        review_token: review.review_token,
                      }),
                    )
                  ) {
                    setCurrentBody(review.after);
                    setBody(review.after);
                    setReview(null);
                    setUpdated(true);
                  }
                }}
              >
                Apply reviewed Facebook text
              </button>
            </div>
          )}
          {updated && <p role="status">Facebook confirmed the text update.</p>}
        </>
      )}
    </details>
  );
}
