import { useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { request, send } from "../../../packages/api-client/src";

interface Report {
  schema_version: number;
  normalized: Record<string, number>;
  semantics: Record<string, string>;
  window?: { start: string; end: string; timezone: string; inclusive?: boolean };
  notice?: string;
  context?: Record<string, unknown>;
  coverage?: { post_error?: { message: string } | null; more_posts?: boolean; posts_returned?: number; [key: string]: unknown };
  tables: { name: string; rows: Record<string, unknown>[] }[];
}
interface Snapshot { id: string; collected_at: string; report: Report }
interface Row { account_id: string; provider: string; account_name: string; active: boolean; latest: Snapshot | null; error: { message: string } | null }
type Run = (action: () => Promise<unknown>) => Promise<boolean>;

export function AccountReports({ run, busy, enabled }: { run: Run; busy: boolean; enabled: boolean }) {
  const [selected, setSelected] = useState<string | null>(null);
  const data = useInfiniteQuery({ queryKey: ["account-reports"], initialPageParam: 0,
    queryFn: ({ pageParam }) => request<Row[]>(`/reports/accounts?offset=${pageParam}`),
    getNextPageParam: (last, pages) => last.length === 100 ? pages.length * 100 : undefined, enabled });
  const history = useQuery({ queryKey: ["account-report-history", selected],
    queryFn: () => request<Snapshot[]>(`/reports/accounts/${selected}/history`), enabled: !!selected });
  const rows = data.data?.pages.flat() ?? [];
  if (!rows.length && !data.error) return null;
  return <section aria-label="Account reports">
    <h2>Account reports</h2>
    <p className="muted">Saved account and channel reports. Dates and metric meanings stay with each snapshot.</p>
    {data.error && <p role="alert">{data.error.message}</p>}
    {rows.map(row => <article className="panel" key={row.account_id}>
      <div className="section-head"><div><h3>{row.account_name}</h3><p>{row.provider.replaceAll("_", " ")}</p></div>
        <div className="toolbar"><button disabled={busy || !row.active} onClick={() => run(() => send(`/reports/accounts/${row.account_id}/refresh`))}>Collect account report</button>
          <button onClick={() => setSelected(selected === row.account_id ? null : row.account_id)}>History</button>
          {row.latest && <button disabled={busy} onClick={() => run(async () => {
            const value = await request(`/reports/accounts/${row.account_id}/export`);
            const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }));
            const link = document.createElement("a"); link.href = url; link.download = "account-report.json";
            document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
          })}>Export JSON</button>}
        </div>
      </div>
      {!row.active && <p>Reconnect this account to collect fresh data.</p>}
      {row.error && <p className="notice error">{row.error.message}{row.latest ? " Last successful report retained below." : ""}</p>}
      {row.latest ? <ReportView snapshot={row.latest} /> : <p>No report collected yet. Connect the account, then collect a report.</p>}
      {selected === row.account_id && <details open><summary>Historical snapshots · latest 50</summary>
        {history.error && <p role="alert">{history.error.message}</p>}
        {history.data?.map(snapshot => <ReportView key={snapshot.id} snapshot={snapshot} />)}
      </details>}
    </article>)}
    {data.hasNextPage && <button disabled={data.isFetchingNextPage} onClick={() => data.fetchNextPage()}>Load more reporting accounts</button>}
  </section>;
}

function cell(value: unknown) {
  if (value === null || value === undefined) return "Unavailable";
  if (typeof value === "number") return value.toLocaleString();
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return typeof value === "string" ? value : JSON.stringify(value);
}

function ReportView({ snapshot }: { snapshot: Snapshot }) {
  const report = snapshot.report;
  return <div>
    <small>Collected {new Date(snapshot.collected_at).toLocaleString()}</small>
    {report.window && <p>Daily report: {report.window.start} – {report.window.end} · {report.window.timezone}</p>}
    {report.notice && <p className="muted">{report.notice}</p>}
    {Object.keys(report.normalized).length > 0 && <table className="metrics"><thead><tr><th>Metric</th><th>Value</th><th>Meaning</th></tr></thead><tbody>
      {Object.entries(report.normalized).map(([key, value]) => <tr key={key}><td>{key.replaceAll("_", " ")}</td><td>{cell(value)}</td><td>{report.semantics[key]}</td></tr>)}
    </tbody></table>}
    {report.coverage?.post_error && <p className="notice error">Post data incomplete: {report.coverage.post_error.message}</p>}
    {report.coverage?.more_posts && <p>Showing {report.coverage.posts_returned} recent posts; older posts remain outside this snapshot.</p>}
    {report.tables.map(table => {
      const columns = [...new Set(table.rows.flatMap(row => Object.keys(row)))];
      return <details key={table.name}><summary>{table.name} · {table.rows.length} rows</summary>
        {!table.rows.length ? <p>No rows returned. This does not establish zero activity.</p> : <div style={{ overflowX: "auto" }}><table className="metrics"><thead><tr>
          {columns.map(column => <th key={column}>{column.replaceAll("_", " ")}</th>)}
        </tr></thead><tbody>{table.rows.map((row, index) => <tr key={index}>{columns.map(column => <td key={column}>{cell(row[column])}</td>)}</tr>)}</tbody></table></div>}
      </details>;
    })}
    {(report.coverage || report.context) && <details><summary>Source and report coverage</summary>
      {Object.entries({ ...report.context, ...report.coverage }).map(([key, value]) => <p key={key}><strong>{key.replaceAll("_", " ")}: </strong>{cell(value)}</p>)}
    </details>}
  </div>;
}
