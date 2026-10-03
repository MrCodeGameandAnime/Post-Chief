import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { request } from "../../../packages/api-client/src";
import type { Asset } from "../../../packages/shared-types/src";

export function TikTokOptions({ accountId, assets, body, consent, change }: {
  accountId: string; assets: Asset[]; body: string; consent: boolean; change: (value: boolean) => void;
}) {
  const creator = useQuery({ queryKey: ["tiktok-creator", accountId],
    queryFn: () => request<{ display_name: string; username?: string }>("/connections/tiktok/" + accountId + "/creator"),
    staleTime: 0, retry: false });
  const signature = JSON.stringify([accountId, body, assets.map(a => a.id)]);
  const previous = useRef(signature);
  useEffect(() => {
    if (previous.current !== signature) { previous.current = signature; change(false); }
  }, [signature, change]);
  return <fieldset>
    <legend>TikTok inbox transfer</legend>
    {creator.isPending && <p>Loading the current creator…</p>}
    {creator.error && <p role="alert">{creator.error.message}</p>}
    {creator.data && <p>Send to {creator.data.display_name}{creator.data.username && " (@" + creator.data.username + ")"}</p>}
    {assets.filter(a => a.mime_type === "video/mp4").map(a => <video key={a.id} controls preload="metadata" src={a.file_url} style={{ maxWidth: "100%" }} />)}
    <p>Finish the caption, audience, interactions and posting in TikTok. This transfer sends a video to your inbox and can take a few minutes.</p>
    <p>Caption to use in TikTok: {body || "No caption"}</p>
    <label className="check"><input type="checkbox" checked={consent} disabled={!creator.data || !!creator.error}
      onChange={e => change(e.target.checked)} />I reviewed the video and consent to sending it to this creator’s TikTok inbox.</label>
  </fieldset>;
}
