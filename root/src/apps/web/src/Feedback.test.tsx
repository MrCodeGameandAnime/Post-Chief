// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { Feedback } from "./Feedback";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
test("reviews workspace files and writes the exact preview", async () => {
  const preview = {
    revision: 3,
    digest: "d".repeat(64),
    base_commit: "a".repeat(40),
    workspace: "owner/social",
    branch: "main",
    unchanged: false,
    files: [
      { path: "posts/campaign.md", content: "Post record" },
      { path: "docs/CONTENT_LEDGER.md", content: "Ledger" },
      { path: "docs/ANALYTICS.md", content: "Metrics" },
    ],
  };
  const fetch = vi.fn(
    async (url: string, options?: RequestInit) =>
      new Response(
        JSON.stringify(
          options?.method === "POST" ? { sha: "b".repeat(40) } : preview,
        ),
      ),
  );
  vi.stubGlobal("fetch", fetch);
  render(
    <Feedback
      campaignId="campaign"
      busy={false}
      run={async (action) => {
        await action();
        return true;
      }}
    />,
  );
  expect(
    screen.queryByRole("button", { name: "Write reviewed feedback" }),
  ).toBeNull();
  fireEvent.click(
    screen.getByRole("button", { name: "Preview GitHub feedback" }),
  );
  expect(await screen.findByText("docs/CONTENT_LEDGER.md")).toBeTruthy();
  expect(screen.getByText("docs/ANALYTICS.md")).toBeTruthy();
  fireEvent.click(
    screen.getByRole("button", { name: "Write reviewed feedback" }),
  );
  expect(await screen.findByRole("status")).toHaveProperty(
    "textContent",
    "Written in commit " + "b".repeat(40),
  );
  expect(JSON.parse(String(fetch.mock.calls[1][1]?.body))).toEqual({
    revision: 3,
    digest: preview.digest,
    base_commit: preview.base_commit,
  });
});
