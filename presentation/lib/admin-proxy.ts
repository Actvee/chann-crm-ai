import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { ApplicationError, callApplication } from "@/lib/api";

export const ADMIN_COOKIE = "chann_admin_session";

/** Forward a browser call from the admin console to the Application tier
 *  as the signed-in operator (cookie → bearer). Never exposes the token
 *  to client code. */
export async function adminForward(
  path: string,
  init: { method: string; body?: string },
): Promise<NextResponse> {
  const token = (await cookies()).get(ADMIN_COOKIE)?.value;
  if (!token) {
    return NextResponse.json({ detail: "not signed in" }, { status: 401 });
  }
  try {
    const result = await callApplication<unknown>(path, {
      method: init.method,
      headers: { Authorization: `Bearer ${token}` },
      body: init.body,
    });
    return NextResponse.json(result ?? { ok: true });
  } catch (error) {
    if (error instanceof ApplicationError) {
      // The Application tier's own body, so the console can say WHY —
      // "member is not active", "locked until …" — instead of "failed"
      // (review D11/D13, 6 Sep 2026).
      const body =
        error.body && typeof error.body === "object"
          ? (error.body as Record<string, unknown>)
          : { detail: typeof error.body === "string" ? error.body : "admin request failed" };
      return NextResponse.json(body, { status: error.status });
    }
    return NextResponse.json({ detail: "application tier unreachable" }, { status: 503 });
  }
}
