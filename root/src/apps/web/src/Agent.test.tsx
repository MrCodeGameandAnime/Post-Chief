// @vitest-environment jsdom
import { render, screen, cleanup } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Agent } from "./Agent";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
test("shows owner approval payload and conservative autonomy without a stored token", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async (url: string) =>
        new Response(
          JSON.stringify(
            url.endsWith("/settings/autonomy")
              ? {
                  policies: { "campaigns:publish": "APPROVAL" },
                  scopes: ["campaigns:read"],
                }
              : url.endsWith("/approvals")
                ? [
                    {
                      id: "approval",
                      action: "campaign.publish",
                      status: "pending",
                      expires_at: "2099-01-01T00:00:00Z",
                      payload: {
                        target_id: "campaign",
                        context: { revision: 3 },
                        data: {},
                      },
                    },
                  ]
                : [],
          ),
        ),
    ),
  );
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <Agent
        run={async (action) => {
          await action();
          return true;
        }}
        busy={false}
      />
    </QueryClientProvider>,
  );
  expect(
    await screen.findByRole("button", { name: "Approve exact action" }),
  ).toBeTruthy();
  expect(screen.getByRole("combobox").textContent).toContain("DISABLED");
  expect(screen.getByText(/"revision": 3/)).toBeTruthy();
  expect(screen.queryByLabelText("New agent token")).toBeNull();
});
