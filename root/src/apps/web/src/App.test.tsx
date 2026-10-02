// @vitest-environment jsdom
import React from "react";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App } from "./App";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("saves a draft with selected destination and session CSRF", async () => {
  const user = userEvent.setup();
  const writes: RequestInit[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, options: RequestInit) => {
      if (options.method === "POST") writes.push(options);
      const data = url.endsWith("/auth/me")
        ? { email: "owner@example.test", csrf: "draft-csrf" }
        : url.endsWith("/connections")
          ? [
              {
                id: "account",
                provider: "bluesky",
                name: "404 Builds",
                active: true,
                remote_id: "did:plc:test",
                expires_at: null,
              },
            ]
          : [];
      return new Response(JSON.stringify(data), { status: 200 });
    }),
  );
  mount();
  await userEvent.click(
    await screen.findByRole("button", { name: "New campaign" }),
  );
  await user.click(screen.getByLabelText("Campaign title"));
  await user.paste("Launch notes");
  await user.click(screen.getByLabelText("Master copy"));
  await user.paste("A useful update");
  await userEvent.click(
    await screen.findByRole("checkbox", { name: "bluesky · 404 Builds" }),
  );
  await userEvent.click(screen.getByRole("button", { name: "Save draft" }));
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(JSON.parse(writes[0].body as string)).toMatchObject({
    title: "Launch notes",
    body: "A useful update",
    account_ids: ["account"],
  });
  expect(new Headers(writes[0].headers).get("X-CSRF-Token")).toBe("draft-csrf");
});
function mount() {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <App />
    </QueryClientProvider>,
  );
}
test("requires owner login and shows authentication failures", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: "Sign in required" }), {
        status: 401,
      }),
    ),
  );
  mount();
  expect(
    screen.getByRole("heading", { name: "Post Chief", level: 1 }),
  ).toBeTruthy();
  await screen.findByLabelText("Email");
  await userEvent.type(screen.getByLabelText("Email"), "owner@example.test");
  await userEvent.type(screen.getByLabelText("Password"), "incorrect");
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
  expect(await screen.findByRole("alert")).toBeTruthy();
});
test("shows campaign once, its destinations, and a planner view", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async (url: string) =>
        new Response(
          JSON.stringify(
            url.endsWith("/auth/me")
              ? { email: "owner@example.test", csrf: "csrf" }
              : url.endsWith("/campaigns")
                ? [
                    {
                      id: "campaign",
                      title: "One release",
                      body: "Shipped",
                      status: "draft",
                      revision: 1,
                      asset_ids: [],
                      overrides: {},
                      scheduled_at: null,
                      publications: [
                        {
                          id: "pub",
                          account_id: "account",
                          provider: "bluesky",
                          account_name: "404 Builds",
                          status: "pending",
                          attempts: 0,
                          error: null,
                          url: null,
                        },
                      ],
                    },
                  ]
                : [],
          ),
          { status: 200 },
        ),
    ),
  );
  mount();
  await userEvent.click(await screen.findByRole("button", { name: "Content" }));
  expect(
    await screen.findByRole("button", { name: "One release" }),
  ).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Planner" }));
  expect(screen.getByRole("button", { name: "Month" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Week" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "List" })).toBeTruthy();
});

test.each([
  {
    status: "draft",
    publicationStatus: "pending",
    attempts: 0,
    count: "0 delivery runs",
  },
  {
    status: "failed",
    publicationStatus: "failed",
    attempts: 1,
    count: "1 delivery run",
  },
  {
    status: "published",
    publicationStatus: "published",
    attempts: 3,
    count: "3 delivery runs",
  },
])(
  "explains delivery runs and retained copy for $status",
  async ({ status, publicationStatus, attempts, count }) => {
    const campaign = {
      id: "campaign",
      title: "Delivery example",
      body: "Original caption",
      status,
      revision: 1,
      asset_ids: [],
      overrides: {},
      scheduled_at: null,
      publications: [
        {
          id: "pub",
          account_id: "account",
          provider: "instagram",
          account_name: "404.builds.dev",
          status: publicationStatus,
          attempts,
          error: null,
          url:
            status === "published"
              ? "https://www.instagram.com/p/example/"
              : null,
        },
      ],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async (url: string) =>
          new Response(
            JSON.stringify(
              url.endsWith("/auth/me")
                ? { email: "owner@example.test", csrf: "csrf" }
                : url.endsWith("/campaigns")
                  ? [campaign]
                  : [],
            ),
          ),
      ),
    );
    mount();
    await userEvent.click(
      await screen.findByRole("button", { name: "Delivery example" }),
    );
    expect(await screen.findByText("Delivery details")).toBeTruthy();
    await userEvent.click(screen.getByText("Delivery details"));
    expect(screen.getByText(count)).toBeTruthy();
    expect(
      screen.getByText(/Multiple runs can produce a single published post/),
    ).toBeTruthy();
    const copy = screen.getByLabelText(
      attempts ? "Original master copy" : "Master copy",
    ) as HTMLTextAreaElement;
    expect(copy.value).toBe("Original caption");
    expect(copy.disabled).toBe(attempts > 0);
    if (attempts)
      expect(
        screen.getByText(
          /Edits made directly on a platform aren’t synced back/,
        ),
      ).toBeTruthy();
    else
      expect(
        screen.queryByText(/Edits made directly on a platform/),
      ).toBeNull();
    if (status === "published")
      expect(
        screen
          .getByRole("link", { name: "View published post ↗" })
          .getAttribute("href"),
      ).toBe("https://www.instagram.com/p/example/");
  },
);
