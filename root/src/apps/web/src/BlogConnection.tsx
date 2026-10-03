import { useState } from "react";
import { send } from "../../../packages/api-client/src";

type Run = (action: () => Promise<unknown>) => Promise<boolean>;
type Feed = { url: string; name: string };

export function BlogConnection({ run, busy }: { run: Run; busy: boolean }) {
  const [url, setUrl] = useState("");
  const [feeds, setFeeds] = useState<Feed[]>([]);
  const [discovered, setDiscovered] = useState(false);
  return <form onSubmit={event => {
    event.preventDefault();
    run(() => send("/connections/blog", { url }));
  }}>
    <p className="muted">Connect a public RSS or Atom feed for saved source snapshots in Analytics. You can also discover advertised feeds from a website. Feed text is source material; this connection cannot publish campaigns.</p>
    <label>Website or feed URL<input type="url" required maxLength={2048} value={url} placeholder="https://example.com/feed/"
      onChange={event => { setUrl(event.target.value); setDiscovered(false); setFeeds([]); }} /></label>
    <div className="toolbar">
      <button type="button" disabled={busy || !url} onClick={() => run(async () => {
        const result = await send<{ feeds: Feed[] }>("/connections/blog/discover", { url });
        setFeeds(result.feeds); setDiscovered(true);
      })}>Discover feeds</button>
      <button disabled={busy || !url}>Connect feed URL</button>
    </div>
    {discovered && !feeds.length && <p>No RSS/Atom links were advertised. Enter the feed's direct public HTTPS URL.</p>}
    {feeds.map(feed => <div key={feed.url}><p>{feed.name} · {feed.url}</p>
      <button type="button" disabled={busy} onClick={() => run(() => send("/connections/blog", { url: feed.url }))}>Connect {feed.name}</button>
    </div>)}
  </form>;
}
