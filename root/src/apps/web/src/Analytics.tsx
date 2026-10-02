import { useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { request, send } from "../../../packages/api-client/src";

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

export function Analytics({ run, busy }: { run: Run; busy: boolean }) {
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
      <p className="muted">
        Latest native metrics, grouped by destination. Missing values are
        unavailable. Counts keep their provider meaning.
      </p>
      {data.isPending && <p>Loading analytics…</p>}
      {data.error && <p role="alert">{data.error.message}</p>}
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
