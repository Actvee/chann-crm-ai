// The single outbound seam of the Presentation Tier.
//
// Every network call from this tier goes through here, so the boundary rule
// (Presentation -> Application only) is enforceable by reading one file
// rather than auditing every component.

const BASE = process.env.APPLICATION_BASE_URL ?? "http://localhost:8080";

export class ApplicationError extends Error {
  /** The Application Tier's own error body (its `detail`), when it sent
   *  one. The proxy relays it so a page can say WHY — "duplicate phone,
   *  it is C-2026-0012", "missing: address, appointment" — instead of a
   *  status code. Every page that branched on the detail was dead code
   *  until 6 Sep 2026 because this seam swallowed the body. */
  constructor(public readonly status: number, public readonly body: unknown = undefined) {
    super(`application tier returned ${status}`);
  }
}

export type ApplicationResponse<T> = {
  data: T;
  status: number;
};

export async function callApplicationResponse<T>(
  path: string,
  init: RequestInit = {},
): Promise<ApplicationResponse<T>> {
  if (!path.startsWith("/api/v1")) {
    throw new Error(
      `Presentation may only call the Application Tier /api/v1 surface, got: ${path}`,
    );
  }
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    let body: unknown = undefined;
    try {
      const text = await res.text();
      try {
        body = text ? JSON.parse(text) : undefined;
      } catch {
        body = text ? { detail: text.slice(0, 500) } : undefined;
      }
    } catch {
      body = undefined;
    }
    throw new ApplicationError(res.status, body);
  }
  if (res.status === 204) {
    return { data: undefined as T, status: res.status };
  }
  return { data: (await res.json()) as T, status: res.status };
}

/**
 * The Application Tier's response, untouched.
 *
 * `callApplicationResponse` parses JSON, which is right for almost every
 * endpoint and wrong for the ones that return a PDF: res.json() throws on
 * binary, the proxy catches it, and the person sees a 503 for a request
 * the Application Tier answered with 200 and a perfectly good document.
 *
 * That failure left no trace anywhere — no error in the Application Tier
 * (it succeeded) and no 503 in its access log (it never returned one) —
 * which is why it survived several rounds of looking.
 */
export async function callApplicationRaw(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  if (!path.startsWith("/api/v1/")) {
    throw new Error(
      `Presentation may only call the Application Tier /api/v1 surface, got: ${path}`,
    );
  }
  return fetch(`${BASE}${path}`, { ...init, cache: "no-store" });
}

export async function callApplication<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  return (await callApplicationResponse<T>(path, init)).data;
}
