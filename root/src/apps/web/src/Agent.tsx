import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { request, send } from "../../../packages/api-client/src";

interface Key {
  id: string;
  name: string;
  scopes: string[];
  active: boolean;
}
interface Approval {
  id: string;
  action: string;
  payload: Record<string, unknown>;
  status: string;
  expires_at: string;
}
interface Run {
  id: string;
  summary: string;
  status: string;
  created_at: string;
}
interface Audit {
  id: string;
  action: string;
  actor_id: string;
  created_at: string;
  details: Record<string, unknown>;
}
type Mode = "AUTO" | "APPROVAL" | "DISABLED";
type Execute = (action: () => Promise<unknown>) => Promise<boolean>;

export function Agent({ run, busy }: { run: Execute; busy: boolean }) {
  const settings = useQuery({
    queryKey: ["autonomy"],
    queryFn: () =>
      request<{ policies: Record<string, Mode>; scopes: string[] }>(
        "/settings/autonomy",
      ),
  });
  const keys = useQuery({
    queryKey: ["agent-keys"],
    queryFn: () => request<Key[]>("/agent/keys"),
  });
  const approvals = useQuery({
    queryKey: ["approvals"],
    queryFn: () => request<Approval[]>("/approvals"),
    refetchInterval: 15000,
  });
  const reports = useQuery({
    queryKey: ["agent-runs"],
    queryFn: () => request<Run[]>("/agent/runs"),
    refetchInterval: 15000,
  });
  const audit = useQuery({
    queryKey: ["audit"],
    queryFn: () => request<Audit[]>("/audit"),
    refetchInterval: 15000,
  });
  const [scopes, setScopes] = useState([
    "campaigns:read",
    "github:read",
    "media:read",
    "analytics:read",
    "audit:read",
    "agent:report",
  ]);
  const [token, setToken] = useState("");
  return (
    <>
      {[settings.error, keys.error, approvals.error, reports.error, audit.error]
        .filter(Boolean)
        .map((error, i) => (
          <p key={i} role="alert">
            {error?.message}
          </p>
        ))}
      <div className="panel">
        <h3>Agent keys</h3>
        <p className="muted">
          Keys inherit the selected scopes and organization autonomy. Store a
          new token privately; it is shown once.
        </p>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            const name = new FormData(e.currentTarget).get("name");
            await run(async () => {
              const result = await send<{ token: string }>("/agent/keys", {
                name,
                scopes,
              });
              setToken(result.token);
            });
          }}
        >
          <label>
            Key name
            <input name="name" required maxLength={200} />
          </label>
          <fieldset>
            <legend>Allowed scopes</legend>
            {settings.data?.scopes.map((scope) => (
              <label key={scope} className="check">
                <input
                  type="checkbox"
                  checked={scopes.includes(scope)}
                  onChange={(e) =>
                    setScopes(
                      e.target.checked
                        ? [...scopes, scope]
                        : scopes.filter((s) => s !== scope),
                    )
                  }
                />
                {scope}
              </label>
            ))}
          </fieldset>
          <button disabled={busy || !scopes.length}>Create agent key</button>
        </form>
        {token && (
          <div className="notice">
            <label>
              New agent token
              <textarea readOnly value={token} />
            </label>
            <button onClick={() => setToken("")}>Dismiss token</button>
          </div>
        )}
        {keys.data?.map((key) => (
          <article className="account" key={key.id}>
            <strong>{key.name}</strong>
            <p>
              {key.active ? "Active" : "Revoked"} · {key.scopes.join(", ")}
            </p>
            {key.active && (
              <button
                disabled={busy}
                onClick={() =>
                  run(() => send("/agent/keys/" + key.id, undefined, "DELETE"))
                }
              >
                Revoke {key.name}
              </button>
            )}
          </article>
        ))}
      </div>
      <div className="panel">
        <h3>Autonomy</h3>
        <p className="muted">
          AUTO allows scoped agents to act. APPROVAL requires an exact, expiring
          owner approval. DISABLED blocks new agent actions. Existing schedules
          can be cancelled in Content.
        </p>
        {Object.entries(settings.data?.policies ?? {}).map(([scope, mode]) => (
          <label key={scope}>
            {scope}
            <select
              value={mode}
              disabled={busy}
              onChange={(e) =>
                run(() =>
                  send(
                    "/settings/autonomy",
                    { policies: { [scope]: e.target.value } },
                    "PUT",
                  ),
                )
              }
            >
              {(["AUTO", "APPROVAL", "DISABLED"] as const).map((value) => (
                <option key={value}>{value}</option>
              ))}
            </select>
          </label>
        ))}
      </div>
      <div className="panel">
        <h3>Items needing approval</h3>
        <p className="muted">
          Review the exact payload and revision before approving. The agent must
          resubmit the same action with this approval ID.
        </p>
        {!approvals.data?.some((a) => a.status === "pending") && (
          <p>No pending approvals.</p>
        )}
        {approvals.data
          ?.filter((a) => a.status === "pending")
          .map((approval) => (
            <article className="publication" key={approval.id}>
              <strong>{approval.action}</strong>
              <p>Expires {new Date(approval.expires_at).toLocaleString()}</p>
              <pre>{JSON.stringify(approval.payload, null, 2)}</pre>
              <div className="toolbar">
                <button
                  disabled={busy || new Date(approval.expires_at) <= new Date()}
                  onClick={() =>
                    run(() => send("/approvals/" + approval.id + "/approve"))
                  }
                >
                  Approve exact action
                </button>
                <button
                  disabled={busy || new Date(approval.expires_at) <= new Date()}
                  onClick={() =>
                    run(() => send("/approvals/" + approval.id + "/reject"))
                  }
                >
                  Reject action
                </button>
              </div>
            </article>
          ))}
      </div>
      <div className="panel">
        <h3>Recent run summaries</h3>
        <p className="muted">
          Concise observations and outcomes supplied by the maintainer.
        </p>
        {reports.data?.map((report) => (
          <article className="account" key={report.id}>
            <small>
              {new Date(report.created_at).toLocaleString()} · {report.status}
            </small>
            <p>{report.summary}</p>
          </article>
        ))}
      </div>
      <div className="panel">
        <h3>Action log</h3>
        {audit.data?.map((event) => (
          <details key={event.id}>
            <summary>
              {new Date(event.created_at).toLocaleString()} · {event.action}
            </summary>
            <small>Actor {event.actor_id}</small>
            <pre>{JSON.stringify(event.details, null, 2)}</pre>
          </details>
        ))}
      </div>
    </>
  );
}
