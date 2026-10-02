// @vitest-environment jsdom
import { afterEach, expect, test, vi } from "vitest";
import {
  request,
  send,
  setCsrf,
  ApiError,
} from "../../../packages/api-client/src";

afterEach(() => {
  vi.unstubAllGlobals();
  setCsrf("");
});
test("sends CSRF on writes and credentials on all requests without persisting secrets", async () => {
  const fetch = vi.fn().mockResolvedValue(new Response("{}"));
  vi.stubGlobal("fetch", fetch);
  setCsrf("session-csrf");
  await send("/campaigns", { title: "Release" });
  const options = fetch.mock.calls[0][1];
  expect(options.credentials).toBe("include");
  expect(options.headers.get("X-CSRF-Token")).toBe("session-csrf");
  expect(options.headers.get("Content-Type")).toBe("application/json");
  expect(localStorage.length).toBe(0);
});
test("normalizes failed requests and lets browser set multipart content type", async () => {
  const fetch = vi
    .fn()
    .mockResolvedValue(
      new Response(JSON.stringify({ detail: "Reload before editing" }), {
        status: 409,
      }),
    );
  vi.stubGlobal("fetch", fetch);
  await expect(
    request("/assets", { method: "POST", body: new FormData() }),
  ).rejects.toMatchObject({
    status: 409,
    message: "Reload before editing",
  } satisfies Partial<ApiError>);
  expect(fetch.mock.calls[0][1].headers.has("Content-Type")).toBe(false);
});
