// The single outbound seam of the Presentation Tier.
//
// Every network call from this tier goes through here, so the boundary rule
// (Presentation -> Application only) is enforceable by reading one file
// rather than auditing every component.

const BASE = process.env.APPLICATION_BASE_URL ?? "http://localhost:8080";

export class ApplicationError extends Error {
  constructor(public readonly status: number) {
    super(`application tier returned ${status}`);
  }
}

export async function callApplication<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
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
    throw new ApplicationError(res.status);
  }
  return (await res.json()) as T;
}
