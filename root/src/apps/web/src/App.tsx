import { useState, type FormEvent } from "react";
import {
  useQuery,
  useInfiniteQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  request,
  send,
  setCsrf,
  ApiError,
} from "../../../packages/api-client/src";
import type {
  Session,
  Campaign,
  Connection,
  Asset,
  ProviderName,
} from "../../../packages/shared-types/src";
import "./styles.css";
import { Analytics } from "./Analytics";
import { Agent } from "./Agent";
import { Feedback } from "./Feedback";
import {
  AddDestination,
  DestinationDelivery,
  FacebookTextEdit,
} from "./Maintenance";

const providers: ProviderName[] = [
  "bluesky",
  "facebook",
  "instagram",
  "threads",
  "linkedin",
];
const date = (value: string | null, fallback = "Not reported") =>
  value
    ? new Date(value).toLocaleString([], {
        dateStyle: "medium",
        timeStyle: "short",
      })
    : fallback;
const campaignDate = (campaign: Campaign) =>
  date(
    campaign.scheduled_at,
    campaign.status === "draft" ? "Unscheduled draft" : "No scheduled time",
  );
const message = (error: unknown) =>
  error instanceof Error ? error.message : "Something went wrong.";
type Run = (action: () => Promise<unknown>) => Promise<boolean>;

export function App() {
  const cache = useQueryClient();
  const session = useQuery({
    queryKey: ["session"],
    queryFn: async () => {
      try {
        const value = await request<Session>("/auth/me");
        setCsrf(value.csrf);
        return value;
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) return null;
        throw error;
      }
    },
    retry: false,
  });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [page, setPage] = useState("Overview");
  const [editing, setEditing] = useState<Campaign | "new" | null>(null);
  const enabled = !!session.data;
  const campaigns = useInfiniteQuery({
    queryKey: ["campaigns"],
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      request<Campaign[]>(
        pageParam ? `/campaigns?offset=${pageParam}&limit=100` : "/campaigns",
      ),
    getNextPageParam: (last, pages) =>
      last.length === 100 ? pages.length * 100 : undefined,
    enabled,
    refetchInterval: 15000,
  });
  const accounts = useQuery({
    queryKey: ["connections"],
    queryFn: () => request<Connection[]>("/connections"),
    enabled,
  });
  const assets = useQuery({
    queryKey: ["assets"],
    queryFn: () => request<Asset[]>("/assets"),
    enabled,
  });
  const run: Run = async (action) => {
    setError("");
    setBusy(true);
    try {
      await action();
      await cache.invalidateQueries();
      return true;
    } catch (e) {
      setError(message(e));
      if (e instanceof ApiError && e.status === 401)
        await cache.invalidateQueries({ queryKey: ["session"] });
      return false;
    } finally {
      setBusy(false);
    }
  };
  if (session.isPending)
    return (
      <main className="login">
        <h1>Post Chief</h1>
        <p>Loading your workspace…</p>
      </main>
    );
  if (!session.data)
    return (
      <main className="login">
        <div className="mark">PC</div>
        <h1>Post Chief</h1>
        <p>Social publishing for 404 Builds.</p>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            const data = new FormData(e.currentTarget);
            await run(async () => {
              const result = await send<Session>("/auth/login", {
                email: data.get("email"),
                password: data.get("password"),
              });
              setCsrf(result.csrf);
            });
          }}
        >
          <label>
            Email
            <input name="email" type="email" autoComplete="username" required />
          </label>
          <label>
            Password
            <input
              name="password"
              type="password"
              autoComplete="current-password"
              required
            />
          </label>
          <button className="primary" disabled={busy}>
            Sign in
          </button>
        </form>
        {(error || session.error) && (
          <p role="alert">{error || message(session.error)}</p>
        )}
      </main>
    );
  const rows = campaigns.data?.pages.flat() ?? [];
  return (
    <div className="shell">
      <aside>
        <div className="brand">
          <span className="mark">PC</span>
          <div>
            <h1>Post Chief</h1>
            <small>404 BUILDS</small>
          </div>
        </div>
        <nav aria-label="Main">
          {[
            "Overview",
            "Planner",
            "Content",
            "Assets",
            "Analytics",
            "Agent",
            "Connections",
            "GitHub",
          ].map((item) => (
            <button
              key={item}
              aria-current={page === item ? "page" : undefined}
              onClick={() => {
                setPage(item);
                setEditing(null);
              }}
            >
              {item}
            </button>
          ))}
        </nav>
        <div className="owner">
          <small>{session.data.email}</small>
          <button
            disabled={busy}
            onClick={() =>
              run(async () => {
                await send("/auth/logout");
                setCsrf("");
                cache.setQueryData(["session"], null);
                cache.removeQueries({
                  predicate: (query) => query.queryKey[0] !== "session",
                });
                setEditing(null);
              })
            }
          >
            Sign out
          </button>
        </div>
      </aside>
      <main className="workspace">
        <header>
          <div>
            <p className="eyebrow">YOUR PUBLISHING WORKSPACE</p>
            <h2>{editing ? "Campaign editor" : page}</h2>
          </div>
          <button
            className="primary"
            disabled={busy}
            onClick={() => {
              setPage("Content");
              setEditing("new");
            }}
          >
            New campaign
          </button>
        </header>
        {error && (
          <p className="notice error" role="alert">
            {error}
          </p>
        )}
        {[campaigns.error, accounts.error, assets.error]
          .filter(Boolean)
          .map((e, i) => (
            <p className="notice error" role="alert" key={i}>
              {message(e)}
            </p>
          ))}
        <section aria-busy={busy}>
          {editing ? (
            <Editor
              key={editing === "new" ? "new" : editing.id}
              campaign={editing === "new" ? null : editing}
              latestCampaign={
                editing === "new"
                  ? undefined
                  : rows.find((row) => row.id === editing.id)
              }
              accounts={accounts.data ?? []}
              assets={assets.data ?? []}
              run={run}
              busy={busy}
              close={() => setEditing(null)}
            />
          ) : (
            <>
              {page === "Overview" && (
                <>
                  <div className="stats">
                    {[
                      [
                        "Scheduled",
                        rows.filter((r) => r.status === "scheduled").length,
                      ],
                      [
                        "Publishing",
                        rows.filter((r) => r.status === "publishing").length,
                      ],
                      [
                        "Published",
                        rows.filter((r) => r.status === "published").length,
                      ],
                      [
                        "Needs attention",
                        rows.filter((r) =>
                          ["failed", "partial"].includes(r.status),
                        ).length,
                      ],
                    ].map(([label, count]) => (
                      <article key={label}>
                        <span>{label}</span>
                        <strong>{count}</strong>
                      </article>
                    ))}
                  </div>
                  <div className="section-head">
                    <h3>Upcoming & recent</h3>
                    <span>
                      Times shown in{" "}
                      {Intl.DateTimeFormat().resolvedOptions().timeZone}
                    </span>
                  </div>
                  <CampaignList rows={rows.slice(0, 12)} open={setEditing} />
                </>
              )}
              {page === "Content" && (
                <CampaignList rows={rows} open={setEditing} />
              )}
              {page === "Analytics" && <Analytics run={run} busy={busy} />}
              {page === "Agent" && <Agent run={run} busy={busy} />}
              {page === "Planner" && <Planner rows={rows} open={setEditing} />}
              {page === "Assets" && (
                <Assets assets={assets.data ?? []} run={run} busy={busy} />
              )}
              {page === "Connections" && (
                <Connections
                  accounts={accounts.data ?? []}
                  run={run}
                  busy={busy}
                />
              )}
              {page === "GitHub" && <GitHub run={run} busy={busy} />}
            </>
          )}
        </section>
        {campaigns.hasNextPage && (
          <button
            disabled={campaigns.isFetchingNextPage}
            onClick={() => campaigns.fetchNextPage()}
          >
            Load older campaigns
          </button>
        )}
        <footer>
          Post Chief · 404 Builds{" "}
          <span>Each destination keeps its own outcome.</span>
        </footer>
      </main>
    </div>
  );
}

function CampaignList({
  rows,
  open,
}: {
  rows: Campaign[];
  open: (row: Campaign) => void;
}) {
  if (!rows.length)
    return (
      <div className="empty">
        <h3>Room for your next release.</h3>
        <p>Create a campaign when there’s something worth sharing.</p>
      </div>
    );
  return (
    <div className="campaigns">
      {rows.map((row) => (
        <article className="campaign" key={row.id}>
          <div>
            <button className="title-button" onClick={() => open(row)}>
              {row.title}
            </button>
            <p>{row.body.slice(0, 180)}</p>
            <small>{campaignDate(row)}</small>
          </div>
          <div className="destinations">
            {row.publications.map((pub) => (
              <span className="tag" key={pub.id}>
                {pub.provider} · {pub.status}
              </span>
            ))}
            <span className={"status " + row.status}>{row.status}</span>
          </div>
        </article>
      ))}
    </div>
  );
}

function Planner({
  rows,
  open,
}: {
  rows: Campaign[];
  open: (row: Campaign) => void;
}) {
  const [view, setView] = useState("Month");
  const [anchor, setAnchor] = useState(new Date());
  const start = new Date(
    anchor.getFullYear(),
    anchor.getMonth(),
    view === "Month" ? 1 : anchor.getDate() - anchor.getDay(),
  );
  const days =
    view === "Month"
      ? new Date(anchor.getFullYear(), anchor.getMonth() + 1, 0).getDate()
      : 7;
  const navigate = (direction: number) => {
    const value = new Date(anchor);
    if (view === "Month") value.setMonth(value.getMonth() + direction, 1);
    else value.setDate(value.getDate() + direction * 7);
    setAnchor(value);
  };
  return (
    <>
      <div className="toolbar">
        <div className="segmented">
          {["Month", "Week", "List"].map((item) => (
            <button
              key={item}
              aria-pressed={view === item}
              onClick={() => setView(item)}
            >
              {item}
            </button>
          ))}
        </div>
        <button onClick={() => navigate(-1)} aria-label="Previous period">
          ←
        </button>
        <h3>
          {anchor.toLocaleDateString([], { month: "long", year: "numeric" })}
        </h3>
        <button onClick={() => navigate(1)} aria-label="Next period">
          →
        </button>
        <button onClick={() => setAnchor(new Date())}>Today</button>
      </div>
      <p className="muted">
        {Intl.DateTimeFormat().resolvedOptions().timeZone} · One campaign, all
        destinations.
      </p>
      {view === "List" ? (
        <CampaignList
          rows={[...rows].sort((a, b) =>
            (a.scheduled_at ?? "z").localeCompare(b.scheduled_at ?? "z"),
          )}
          open={open}
        />
      ) : (
        <div className="calendar">
          {view === "Month" &&
            Array.from({ length: start.getDay() }, (_, i) => (
              <div className="day blank" key={"blank" + i} />
            ))}
          {Array.from({ length: days }, (_, i) => {
            const day = new Date(start);
            day.setDate(start.getDate() + i);
            return (
              <div className="day" key={i}>
                <time>
                  {day.toLocaleDateString([], {
                    weekday: "short",
                    day: "numeric",
                  })}
                </time>
                {rows
                  .filter(
                    (r) =>
                      r.scheduled_at &&
                      new Date(r.scheduled_at).toDateString() ===
                        day.toDateString(),
                  )
                  .map((row) => (
                    <button key={row.id} onClick={() => open(row)}>
                      <strong>{row.title}</strong>
                      <small>
                        {row.publications.map((p) => p.provider).join(" · ")}
                      </small>
                      <span>{row.status}</span>
                    </button>
                  ))}
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}

function Editor({
  campaign,
  latestCampaign,
  accounts,
  assets,
  run,
  busy,
  close,
}: {
  campaign: Campaign | null;
  latestCampaign?: Campaign;
  accounts: Connection[];
  assets: Asset[];
  run: Run;
  busy: boolean;
  close: () => void;
}) {
  const [title, setTitle] = useState(campaign?.title ?? "");
  const [body, setBody] = useState(campaign?.body ?? "");
  const [destinations, setDestinations] = useState(
    campaign?.publications.map((p) => p.account_id) ?? [],
  );
  const [media, setMedia] = useState(campaign?.asset_ids ?? []);
  const [overrides, setOverrides] = useState(campaign?.overrides ?? {});
  const [schedule, setSchedule] = useState("");
  const current = latestCampaign ?? campaign;
  const revisionChanged = !!campaign && current?.revision !== campaign.revision;
  const attempted = current?.publications.some((p) => p.attempts > 0) ?? false;
  const editable =
    !campaign ||
    (!revisionChanged &&
      current?.status === "draft" &&
      current.publications.every((p) => p.attempts === 0));
  const toggle = (values: string[], id: string) =>
    values.includes(id) ? values.filter((x) => x !== id) : [...values, id];
  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (
      await run(() =>
        send(
          campaign ? "/campaigns/" + campaign.id : "/campaigns",
          {
            title,
            body,
            account_ids: destinations,
            asset_ids: media,
            overrides,
            ...(campaign ? { revision: campaign.revision } : {}),
          },
          campaign ? "PATCH" : "POST",
        ),
      )
    )
      close();
  };
  return (
    <div className="editor">
      <form onSubmit={save}>
        <label>
          Campaign title
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
            maxLength={200}
            disabled={!editable}
          />
        </label>
        <label>
          {revisionChanged
            ? "Opened master copy"
            : attempted
              ? "Original master copy"
              : "Master copy"}
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            rows={7}
            maxLength={20000}
            disabled={!editable}
          />
        </label>
        {revisionChanged && (
          <p className="notice error" role="alert">
            Campaign changed since you opened it. Return to Content and reopen
            it to load the latest saved copy. Your unsaved copy is retained
            here.
          </p>
        )}
        {attempted && !revisionChanged && (
          <p className="muted">
            This is the original campaign copy saved for delivery. Provider copy
            overrides apply to their destinations. Edits made directly on a
            platform aren’t synced back to this record.
          </p>
        )}
        <fieldset disabled={!editable}>
          <legend>Destinations</legend>
          {accounts
            .filter((a) => a.active || destinations.includes(a.id))
            .map((account) => (
              <label className="check" key={account.id}>
                <input
                  type="checkbox"
                  checked={destinations.includes(account.id)}
                  onChange={() =>
                    setDestinations(toggle(destinations, account.id))
                  }
                />
                {account.provider} · {account.name}
                {!account.active ? " · reconnect required" : ""}
              </label>
            ))}
          {!accounts.some((a) => a.active) && (
            <p>Connect an account before saving a campaign.</p>
          )}
        </fieldset>
        <fieldset disabled={!editable}>
          <legend>Media</legend>
          {assets.map((asset) => (
            <label className="check" key={asset.id}>
              <input
                type="checkbox"
                checked={media.includes(asset.id)}
                onChange={() => setMedia(toggle(media, asset.id))}
              />
              {asset.name} <small>{asset.mime_type}</small>
            </label>
          ))}
          {!assets.length && (
            <p>Upload media in Assets, or publish text only.</p>
          )}
        </fieldset>
        <fieldset disabled={!editable}>
          <legend>Provider overrides</legend>
          {providers
            .filter((p) =>
              accounts.some(
                (a) => a.provider === p && destinations.includes(a.id),
              ),
            )
            .map((provider) => (
              <details key={provider}>
                <summary>{provider} copy & media</summary>
                <label>
                  Copy for {provider}
                  <textarea
                    placeholder="Use master copy"
                    value={overrides[provider]?.body ?? ""}
                    onChange={(e) =>
                      setOverrides({
                        ...overrides,
                        [provider]: {
                          ...overrides[provider],
                          body: e.target.value || undefined,
                        },
                      })
                    }
                  />
                </label>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={overrides[provider]?.asset_ids !== undefined}
                    onChange={(e) =>
                      setOverrides({
                        ...overrides,
                        [provider]: {
                          ...overrides[provider],
                          asset_ids: e.target.checked ? [] : undefined,
                        },
                      })
                    }
                  />
                  Use separate media
                </label>
                {overrides[provider]?.asset_ids !== undefined &&
                  assets.map((asset) => (
                    <label className="check" key={asset.id}>
                      <input
                        type="checkbox"
                        checked={overrides[provider]?.asset_ids?.includes(
                          asset.id,
                        )}
                        onChange={() =>
                          setOverrides({
                            ...overrides,
                            [provider]: {
                              ...overrides[provider],
                              asset_ids: toggle(
                                overrides[provider]?.asset_ids ?? [],
                                asset.id,
                              ),
                            },
                          })
                        }
                      />
                      {asset.name}
                    </label>
                  ))}
              </details>
            ))}
        </fieldset>
        <div className="toolbar">
          {editable && (
            <button className="primary" disabled={busy || !destinations.length}>
              Save draft
            </button>
          )}
          <button type="button" onClick={close}>
            Back to content
          </button>
        </div>
      </form>
      {current && (
        <div className="delivery">
          <h3>Delivery</h3>
          <Feedback campaignId={current.id} run={run} busy={busy} />
          <p className="status">{current.status}</p>
          <p>{campaignDate(current)}</p>
          {!revisionChanged &&
            attempted &&
            !current.publications.some((p) =>
              ["pending", "retrying", "processing"].includes(p.status),
            ) && (
              <AddDestination
                key={current.revision}
                campaign={current}
                accounts={accounts}
                assets={assets}
                run={run}
                busy={busy}
                close={close}
              />
            )}
          {!revisionChanged &&
            ["draft", "scheduled"].includes(current.status) &&
            current.publications.every((p) => p.attempts === 0) && (
              <>
                <label>
                  Schedule in your timezone
                  <input
                    type="datetime-local"
                    value={schedule}
                    onChange={(e) => setSchedule(e.target.value)}
                  />
                </label>
                <div className="toolbar">
                  <button
                    disabled={busy || !schedule}
                    onClick={async () => {
                      if (
                        await run(() =>
                          send("/campaigns/" + current.id + "/schedule", {
                            scheduled_at: new Date(schedule).toISOString(),
                          }),
                        )
                      )
                        close();
                    }}
                  >
                    Schedule
                  </button>
                  <button
                    disabled={busy}
                    onClick={async () => {
                      if (
                        await run(() =>
                          send("/campaigns/" + current.id + "/publish"),
                        )
                      )
                        close();
                    }}
                  >
                    Publish now
                  </button>
                </div>
                <small>Save copy changes before scheduling.</small>
              </>
            )}
          {["scheduled", "publishing", "partial"].includes(current.status) && (
            <button
              disabled={busy}
              onClick={async () => {
                if (
                  await run(() => send("/campaigns/" + current.id + "/cancel"))
                )
                  close();
              }}
            >
              Cancel pending delivery
            </button>
          )}
          {current.publications.map((pub) => (
            <article className="publication" key={pub.id}>
              <strong>
                {pub.provider} · {pub.account_name}
              </strong>
              <p>{pub.status}</p>
              {pub.published_at && (
                <p className="muted">Published at {date(pub.published_at)}</p>
              )}
              {pub.url && (
                <a href={pub.url} target="_blank" rel="noreferrer">
                  View published post ↗
                </a>
              )}
              {!revisionChanged &&
                pub.status === "cancelled" &&
                pub.attempts === 0 && (
                  <DestinationDelivery
                    key={current.revision}
                    campaign={current}
                    publication={pub}
                    assets={assets}
                    run={run}
                    busy={busy}
                    close={close}
                  />
                )}
              {pub.status === "published" &&
                pub.provider === "facebook" &&
                pub.provider_id?.includes("_") && (
                  <FacebookTextEdit
                    publicationId={pub.id}
                    run={run}
                    busy={busy}
                  />
                )}
              {pub.status === "published" && pub.provider === "instagram" && (
                <p>
                  Instagram caption edits must be made in Instagram.
                  {pub.url && (
                    <>
                      {" "}
                      <a href={pub.url} target="_blank" rel="noreferrer">
                        Edit on Instagram ↗
                      </a>
                    </>
                  )}
                </p>
              )}
              <details>
                <summary>Delivery details</summary>
                <p>
                  {pub.attempts} delivery {pub.attempts === 1 ? "run" : "runs"}
                </p>
                <p className="muted">
                  Runs include media preparation, processing checks and retries.
                  Multiple runs can produce a single published post.
                </p>
              </details>
              {pub.error && (
                <p className="notice error">
                  {pub.error.message} · {pub.error.action_required}
                </p>
              )}
              {pub.status === "failed" &&
                pub.error?.action_required !== "RECONCILE" && (
                  <button
                    disabled={busy}
                    onClick={async () => {
                      if (
                        await run(() =>
                          send("/publications/" + pub.id + "/retry"),
                        )
                      )
                        close();
                    }}
                  >
                    Retry this destination
                  </button>
                )}
              {pub.error?.action_required === "RECONCILE" && (
                <Reconcile id={pub.id} run={run} busy={busy} close={close} />
              )}
            </article>
          ))}
        </div>
      )}
    </div>
  );
}

function Reconcile({
  id,
  run,
  busy,
  close,
}: {
  id: string;
  run: Run;
  busy: boolean;
  close: () => void;
}) {
  const [resolution, setResolution] = useState("published");
  const [providerId, setProviderId] = useState("");
  const [checked, setChecked] = useState(false);
  return (
    <details>
      <summary>Resolve uncertain delivery</summary>
      <p>Check the platform before resolving this attempt.</p>
      <label>
        Result
        <select
          value={resolution}
          onChange={(e) => setResolution(e.target.value)}
        >
          <option value="published">An existing post is published</option>
          <option value="not_published">No post was published</option>
        </select>
      </label>
      {resolution === "published" && (
        <label>
          Existing provider ID
          <input
            value={providerId}
            onChange={(e) => setProviderId(e.target.value)}
          />
        </label>
      )}
      <label className="check">
        <input
          type="checkbox"
          checked={checked}
          onChange={(e) => setChecked(e.target.checked)}
        />
        I checked the platform and confirmed this result.
      </label>
      <button
        disabled={
          busy || !checked || (resolution === "published" && !providerId.trim())
        }
        onClick={async () => {
          if (
            await run(() =>
              send("/publications/" + id + "/reconcile", {
                resolution,
                ...(resolution === "published"
                  ? { provider_id: providerId }
                  : {}),
              }),
            )
          )
            close();
        }}
      >
        Record resolution
      </button>
    </details>
  );
}

function Assets({
  assets,
  run,
  busy,
}: {
  assets: Asset[];
  run: Run;
  busy: boolean;
}) {
  return (
    <>
      <div className="toolbar">
        <label className="upload">
          Upload media
          <input
            type="file"
            accept="image/png,image/jpeg,image/webp,image/gif,image/svg+xml,video/mp4"
            disabled={busy}
            onChange={async (e) => {
              const file = e.target.files?.[0];
              if (!file) return;
              const data = new FormData();
              data.append("file", file);
              await run(() =>
                request("/assets", { method: "POST", body: data }),
              );
              e.target.value = "";
            }}
          />
        </label>
      </div>
      <div className="asset-grid">
        {assets.map((asset) => (
          <article key={asset.id}>
            <div className="asset-preview">
              {asset.mime_type.startsWith("image/") &&
              asset.mime_type !== "image/svg+xml" ? (
                <img src={asset.file_url} alt={asset.name} />
              ) : (
                <span>{asset.mime_type === "video/mp4" ? "VIDEO" : "SVG"}</span>
              )}
            </div>
            <h3>{asset.name}</h3>
            <small>
              {asset.source} · {(asset.byte_size / 1024).toFixed(0)} KB
            </small>
            <div className="toolbar">
              <a href={asset.file_url}>Download</a>
              <button
                disabled={busy}
                onClick={() =>
                  run(() => send("/assets/" + asset.id, undefined, "DELETE"))
                }
              >
                Remove
              </button>
            </div>
          </article>
        ))}
      </div>
      <form
        className="panel"
        onSubmit={(e) => {
          e.preventDefault();
          const data = new FormData(e.currentTarget);
          run(() =>
            send("/assets/github", {
              repo: data.get("repo"),
              path: data.get("path"),
            }),
          );
        }}
      >
        <h3>Import from an authorized GitHub repository</h3>
        <label>
          Repository
          <input name="repo" placeholder="owner/repository" required />
        </label>
        <label>
          Asset path
          <input name="path" placeholder="assets/release.png" required />
        </label>
        <button disabled={busy}>Import asset</button>
      </form>
    </>
  );
}

function Connections({
  accounts,
  run,
  busy,
}: {
  accounts: Connection[];
  run: Run;
  busy: boolean;
}) {
  const [organizations, setOrganizations] = useState<
    Record<string, { id: string; name: string }[]>
  >({});
  return (
    <>
      <p className="muted">
        Connect the accounts you want to publish to. App permissions determine
        available capabilities.
      </p>
      <div className="connection-grid">
        {providers.map((provider) => (
          <article className="panel" key={provider}>
            <h3>{provider}</h3>
            {accounts
              .filter((a) => a.provider === provider)
              .map((account) => (
                <div className="account" key={account.id}>
                  <strong>{account.name}</strong>
                  <p>
                    {!account.active
                      ? "Reconnect required"
                      : account.expires_at &&
                          new Date(account.expires_at) < new Date()
                        ? "Token expired"
                        : "Connected"}
                  </p>
                  {account.expires_at && (
                    <small>Token expiration: {date(account.expires_at)}</small>
                  )}
                  {account.active && (
                    <button
                      disabled={busy}
                      onClick={() =>
                        run(() =>
                          send(
                            "/connections/" + account.id,
                            undefined,
                            "DELETE",
                          ),
                        )
                      }
                    >
                      Disconnect {account.name}
                    </button>
                  )}
                  {provider === "linkedin" &&
                    account.remote_id.startsWith("urn:li:person:") &&
                    account.active && (
                      <>
                        <button
                          disabled={busy}
                          onClick={() =>
                            run(async () =>
                              setOrganizations({
                                ...organizations,
                                [account.id]: await request(
                                  "/connections/linkedin/" +
                                    account.id +
                                    "/organizations",
                                ),
                              }),
                            )
                          }
                        >
                          Discover company pages
                        </button>
                        {organizations[account.id]?.map((org) => (
                          <button
                            key={org.id}
                            disabled={busy}
                            onClick={() =>
                              run(() =>
                                send(
                                  "/connections/linkedin/" +
                                    account.id +
                                    "/organizations",
                                  { organization: org.id },
                                ),
                              )
                            }
                          >
                            Connect {org.name}
                          </button>
                        ))}
                      </>
                    )}
                </div>
              ))}
            {provider === "bluesky" ? (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  const data = new FormData(e.currentTarget);
                  run(() =>
                    send("/connections/bluesky", {
                      identifier: data.get("identifier"),
                      password: data.get("password"),
                    }),
                  );
                  e.currentTarget.reset();
                }}
              >
                <label>
                  Bluesky handle
                  <input
                    name="identifier"
                    required
                    placeholder="404builds.bsky.social"
                  />
                </label>
                <label>
                  App password
                  <input
                    name="password"
                    type="password"
                    autoComplete="off"
                    required
                  />
                </label>
                <button disabled={busy}>Connect Bluesky</button>
              </form>
            ) : (
              <button
                disabled={busy}
                onClick={() =>
                  run(async () => {
                    const data = await send<{ url: string }>(
                      "/connections/oauth/" +
                        (provider === "facebook" || provider === "instagram"
                          ? "meta"
                          : provider) +
                        "/authorize",
                    );
                    window.location.assign(data.url);
                  })
                }
              >
                Connect {provider}
              </button>
            )}
          </article>
        ))}
      </div>
    </>
  );
}

function GitHub({ run, busy }: { run: Run; busy: boolean }) {
  const workspace = useQuery({
    queryKey: ["workspace"],
    queryFn: async () => {
      try {
        return await request<{
          workspace: string;
          source_repos: string[];
          installation_id: number;
        }>("/github/workspace");
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }
    },
  });
  const [installation, setInstallation] = useState("");
  const [repos, setRepos] = useState<{ full_name: string }[]>([]);
  const [selected, setSelected] = useState("");
  const [sources, setSources] = useState<string[]>([]);
  return (
    <div className="panel">
      <h3>Persistent context in GitHub</h3>
      {workspace.data && (
        <p>
          Workspace: <strong>{workspace.data.workspace}</strong>
          <br />
          Read-only sources: {workspace.data.source_repos.join(", ") || "None"}
        </p>
      )}
      {workspace.error && <p role="alert">{message(workspace.error)}</p>}
      <button
        disabled={busy}
        onClick={() =>
          run(async () => {
            const data = await request<{ url: string }>(
              "/github/installation-link",
            );
            window.location.assign(data.url);
          })
        }
      >
        Install GitHub App
      </button>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          run(async () =>
            setRepos(
              await request(
                "/github/installations/" + installation + "/repositories",
              ),
            ),
          );
        }}
      >
        <label>
          GitHub installation ID
          <input
            type="number"
            min="1"
            value={installation}
            onChange={(e) => setInstallation(e.target.value)}
            required
          />
        </label>
        <button disabled={busy}>Load granted repositories</button>
      </form>
      {repos.length > 0 && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            run(() =>
              send("/github/workspace", {
                installation_id: Number(installation),
                workspace: selected,
                source_repos: sources,
              }),
            );
          }}
        >
          <label>
            Social workspace
            <select
              value={selected}
              onChange={(e) => setSelected(e.target.value)}
              required
            >
              <option value="">Choose a repository</option>
              {repos.map((repo) => (
                <option key={repo.full_name}>{repo.full_name}</option>
              ))}
            </select>
          </label>
          <fieldset>
            <legend>Read-only project sources</legend>
            {repos
              .filter((r) => r.full_name !== selected)
              .map((repo) => (
                <label className="check" key={repo.full_name}>
                  <input
                    type="checkbox"
                    checked={sources.includes(repo.full_name)}
                    onChange={(e) =>
                      setSources(
                        e.target.checked
                          ? [...sources, repo.full_name]
                          : sources.filter((x) => x !== repo.full_name),
                      )
                    }
                  />
                  {repo.full_name}
                </label>
              ))}
          </fieldset>
          <button disabled={busy}>Save GitHub workspace</button>
        </form>
      )}
    </div>
  );
}
