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

test('saves a draft with selected destination and session CSRF', async()=>{
  const writes:RequestInit[]=[];
  vi.stubGlobal('fetch',vi.fn(async(url:string,options:RequestInit)=>{
    if(options.method==='POST')writes.push(options);
    const data=url.endsWith('/auth/me')?{email:'owner@example.test',csrf:'draft-csrf'}:url.endsWith('/connections')?[{id:'account',provider:'bluesky',name:'404 Builds',active:true,remote_id:'did:plc:test',expires_at:null}]:[];
    return new Response(JSON.stringify(data),{status:200});
  }));
  mount();
  await userEvent.click(await screen.findByRole('button',{name:'New campaign'}));
  await userEvent.type(screen.getByLabelText('Campaign title'),'Launch notes');
  await userEvent.type(screen.getByLabelText('Master copy'),'A useful update');
  await userEvent.click(await screen.findByRole('checkbox',{name:'bluesky · 404 Builds'}));
  await userEvent.click(screen.getByRole('button',{name:'Save draft'}));
  await waitFor(()=>expect(writes).toHaveLength(1));
  expect(JSON.parse(writes[0].body as string)).toMatchObject({title:'Launch notes',body:'A useful update',account_ids:['account']});
  expect(new Headers(writes[0].headers).get('X-CSRF-Token')).toBe('draft-csrf');
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
    vi
      .fn()
      .mockResolvedValue(
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
