import { useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { request, send } from "../../../packages/api-client/src";
import type { Campaign } from "../../../packages/shared-types/src";
import { AccountReports } from "./AccountReports";

interface Snapshot {
  id: string;
  collected_at: string;
  metrics: {
    normalized: Record<string, number>;
    semantics: Record<string, string>;
    provider_metrics: Record<string, unknown>;
  };
}
interface Row {
  publication_id: string;
  title: string;
  provider: string;
  account_name: string;
  latest: Snapshot | null;
  error: { message: string; action_required: string } | null;
}
type Run = (action: () => Promise<unknown>) => Promise<boolean>;

export function Analytics({ run, busy, campaigns = [], reportingEnabled = false }: { run: Run; busy: boolean; campaigns?: Campaign[]; reportingEnabled?: boolean }) {
  const [selected, setSelected] = useState<string | null>(null);
  const data = useInfiniteQuery({
    queryKey: ["analytics"],
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      request<Row[]>(`/analytics?offset=${pageParam}`),
    getNextPageParam: (last, pages) =>
      last.length === 100 ? pages.length * 100 : undefined,
    refetchInterval: 30000,
  });
  const history = useQuery({
    queryKey: ["analytics-history", selected],
    queryFn: () => request<Snapshot[]>(`/analytics/publications/${selected}`),
    enabled: !!selected,
  });
  const rows = data.data?.pages.flat() ?? [];
  return (
    <>
      <AccountReports run={run} busy={busy} enabled={reportingEnabled} />
      <p className="muted">
        Latest native metrics, grouped by destination. Missing values are
        unavailable. Counts keep their provider meaning.
      </p>
      {data.isPending && <p>Loading analytics…</p>}
      {data.error && <p role="alert">{data.error.message}</p>}
      {campaigns.flatMap((campaign) => (campaign.external_posts ?? []).map((post) => (
        <article className="panel" key={post.id}>
          <h3>{campaign.title} · X manual handoff</h3>
          <p>Published · reported by you. Native metrics are unavailable for this handoff.</p>
          <a href={post.url} target="_blank" rel="noreferrer">View X post ↗</a>
        </article>
      )))}
      {!data.isPending && !rows.length && (
        <div className="empty">
          <h3>Performance starts with a published post.</h3>
          <p>Native analytics will appear after collection.</p>
        </div>
      )}
      {rows.map((row) => (
        <article className="panel" key={row.publication_id}>
          <div className="section-head">
            <div>
              <h3>{row.title}</h3>
              <p>
                {row.provider} · {row.account_name}
              </p>
            </div>
            <div className="toolbar">
              <button
                disabled={busy}
                onClick={() =>
                  run(() =>
                    send(
                      `/analytics/publications/${row.publication_id}/refresh`,
                    ),
                  )
                }
              >
                Request fresh metrics
              </button>
              <button onClick={() => setSelected(row.publication_id)}>
                History
              </button>
            </div>
          </div>
          {row.provider === "x" && (
            <p className="muted">X metrics refresh only on request and use X API credits.</p>
          )}
          {row.latest ? (
            <>
              <small>
                Collected {new Date(row.latest.collected_at).toLocaleString()}
              </small>
              <MetricTable snapshot={row.latest} />
            </>
          ) : (
            <p>Not yet available</p>
          )}
          {row.error && (
            <p className="notice error">
              {row.error.message} · {row.error.action_required}
            </p>
          )}
          {selected === row.publication_id && (
            <details open>
              <summary>Historical snapshots</summary>
              {history.error && <p role="alert">{history.error.message}</p>}
              {history.data?.map((snapshot) => (
                <div key={snapshot.id}>
                  <h4>{new Date(snapshot.collected_at).toLocaleString()}</h4>
                  <MetricTable snapshot={snapshot} />
                </div>
              ))}
            </details>
          )}
        </article>
      ))}
      {data.hasNextPage && (
        <button
          onClick={() => data.fetchNextPage()}
          disabled={data.isFetchingNextPage}
        >
          Load older publications
        </button>
      )}
    </>
  );
}

function MetricTable({ snapshot }: { snapshot: Snapshot }) {
  return (
    <>
      <table className="metrics">
        <thead>
          <tr>
            <th>Metric</th>
            <th>Value</th>
            <th>Provider meaning</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(snapshot.metrics.normalized).map(([key, value]) => (
            <tr key={key}>
              <td>{key.replaceAll("_", " ")}</td>
              <td>{value.toLocaleString()}</td>
              <td>{snapshot.metrics.semantics[key]}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {!Object.keys(snapshot.metrics.normalized).length && (
        <p>No supported values were returned.</p>
      )}
      <details>
        <summary>Provider-specific metrics</summary>
        <pre>{JSON.stringify(snapshot.metrics.provider_metrics, null, 2)}</pre>
      </details>
    </>
  );
}
