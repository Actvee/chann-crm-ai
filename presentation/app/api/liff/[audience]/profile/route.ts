import { NextResponse } from "next/server";

import { ApplicationError, callApplication } from "@/lib/api";

const AUDIENCES = new Set(["customer", "sales", "technician"]);

/**
 * The signed-in person's own profile, read and edited from the LIFF home
 * (the UI side of the chat's "แก้เบอร์เป็น 08x"). The Application resolves
 * whose profile from the ID token; this proxy adds nothing but the pass-
 * through, same as /me.
 */
async function forward(
  request: Request,
  audience: string,
  init: { method: string; body?: string },
) {
  if (!AUDIENCES.has(audience)) {
    return NextResponse.json({ detail: "unknown LIFF audience" }, { status: 404 });
  }
  try {
    const profile = await callApplication(`/api/v1/liff/${audience}/profile`, {
      method: init.method,
      headers: {
        "X-Liff-ID-Token": request.headers.get("X-Liff-ID-Token") ?? "",
        ...(init.body ? { "Content-Type": "application/json" } : {}),
      },
      body: init.body,
    });
    return NextResponse.json(profile);
  } catch (error) {
    const status = error instanceof ApplicationError ? error.status : 503;
    return NextResponse.json({ detail: "profile request failed" }, { status });
  }
}

export async function GET(
  request: Request,
  { params }: { params: Promise<{ audience: string }> },
) {
  const { audience } = await params;
  return forward(request, audience, { method: "GET" });
}

export async function PATCH(
  request: Request,
  { params }: { params: Promise<{ audience: string }> },
) {
  const { audience } = await params;
  return forward(request, audience, { method: "PATCH", body: await request.text() });
}
