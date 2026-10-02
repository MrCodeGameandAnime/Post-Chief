export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

let csrf = "";
export function setCsrf(value: string) {
  csrf = value;
}
export async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  const method = options.method ?? "GET";
  if (options.body && !(options.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  if (!["GET", "HEAD"].includes(method) && csrf)
    headers.set("X-CSRF-Token", csrf);
  const response = await fetch("/api" + path, {
    ...options,
    headers,
    credentials: "include",
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new ApiError(
      response.status,
      typeof data.detail === "string"
        ? data.detail
        : "The request could not be completed. Check the fields and try again.",
    );
  return data as T;
}
export const send = <T>(path: string, body?: unknown, method = "POST") =>
  request<T>(path, {
    method,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
