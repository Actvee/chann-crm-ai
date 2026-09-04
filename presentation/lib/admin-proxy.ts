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
    const status = error instanceof ApplicationError ? error.status : 503;
    return NextResponse.json({ detail: "admin request failed" }, { status });
  }
}
