// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { Feedback } from "./Feedback";

beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute("open");
  };
});

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
      {
        path: "posts/campaign.md",
        content: "Post record",
        diff: "+Post record",
        change: "created",
        additions: 1,
        deletions: 0,
      },
      {
        path: "docs/CONTENT_LEDGER.md",
        content: "Ledger",
        diff: "-Old ledger\n+Ledger",
        change: "updated",
        additions: 1,
        deletions: 1,
      },
      {
        path: "docs/ANALYTICS.md",
        content: "Metrics",
        diff: "+Metrics",
        change: "updated",
        additions: 1,
        deletions: 0,
      },
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
  expect(
    await screen.findByRole("dialog", { name: "Review GitHub feedback" }),
  ).toBeTruthy();
  expect(
    screen.getByText(
      "3 files · 1 created · 2 updated · 3 lines added · 1 removed",
    ),
  ).toBeTruthy();
  expect(
    screen
      .getByRole("tab", { name: "posts/campaign.md" })
      .getAttribute("aria-selected"),
  ).toBe("true");
  expect(screen.getByText("+Post record")).toBeTruthy();
  fireEvent.keyDown(screen.getByRole("tab", { name: "posts/campaign.md" }), {
    key: "End",
  });
  expect(
    screen
      .getByRole("tab", { name: "docs/ANALYTICS.md" })
      .getAttribute("aria-selected"),
  ).toBe("true");
  expect(document.activeElement).toBe(
    screen.getByRole("tab", { name: "docs/ANALYTICS.md" }),
  );
  fireEvent.click(screen.getByRole("tab", { name: "docs/CONTENT_LEDGER.md" }));
  expect(screen.getByText("-Old ledger")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Full file" }));
  expect(screen.getByText("Ledger")).toBeTruthy();
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

test("unchanged preview closes and returns focus without offering a write", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            revision: 1,
            digest: "d".repeat(64),
            base_commit: "a".repeat(40),
            workspace: "owner/social",
            branch: "main",
            unchanged: true,
            files: [
              {
                path: "posts/campaign.md",
                content: "Original",
                diff: "",
                change: "unchanged",
                additions: 0,
                deletions: 0,
              },
            ],
          }),
        ),
    ),
  );
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
  const trigger = screen.getByRole("button", {
    name: "Preview GitHub feedback",
  });
  fireEvent.click(trigger);
  expect(await screen.findByRole("dialog")).toBeTruthy();
  expect(screen.getByText("No changes in this file.")).toBeTruthy();
  expect(
    screen.queryByRole("button", { name: "Write reviewed feedback" }),
  ).toBeNull();
  fireEvent.click(
    screen.getByRole("button", { name: "Close feedback review" }),
  );
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(trigger);
  expect(document.body.style.overflow).toBe("");
});

test("failed write keeps the review open and asks for a fresh preview", async () => {
  const fetch = vi.fn(
    async (_url: string, options?: RequestInit) =>
      new Response(
        JSON.stringify(
          options?.method === "POST"
            ? { detail: "Feedback changed" }
            : {
                revision: 1,
                digest: "d".repeat(64),
                base_commit: "a".repeat(40),
                workspace: "owner/social",
                branch: "main",
                unchanged: false,
                files: [
                  {
                    path: "posts/campaign.md",
                    content: "<script>unsafe()</script>",
                    diff: "+<script>unsafe()</script>",
                    change: "created",
                    additions: 1,
                    deletions: 0,
                  },
                ],
              },
        ),
        { status: options?.method === "POST" ? 409 : 200 },
      ),
  );
  vi.stubGlobal("fetch", fetch);
  render(
    <Feedback
      campaignId="campaign"
      busy={false}
      run={async (action) => {
        try {
          await action();
          return true;
        } catch {
          return false;
        }
      }}
    />,
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Preview GitHub feedback" }),
  );
  expect(await screen.findByRole("dialog")).toBeTruthy();
  expect(document.querySelector("script")).toBeNull();
  fireEvent.click(
    screen.getByRole("button", { name: "Write reviewed feedback" }),
  );
  expect(await screen.findByRole("alert")).toBeTruthy();
  expect(screen.getByRole("dialog")).toBeTruthy();
  expect(screen.queryByRole("status")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Refresh preview" }));
  expect(fetch).toHaveBeenCalledTimes(3);
});
