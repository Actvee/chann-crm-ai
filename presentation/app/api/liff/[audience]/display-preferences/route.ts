import { NextResponse } from "next/server";

import { ApplicationError, callApplication } from "@/lib/api";

const AUDIENCES = new Set(["customer", "sales", "technician"]);

/** Phase 16.3 — the signed-in person's own display preferences. */
async function forward(
  request: Request,
  audience: string,
  init: { method: string; body?: string },
) {
  if (!AUDIENCES.has(audience)) {
    return NextResponse.json({ detail: "unknown LIFF audience" }, { status: 404 });
  }
  try {
    const result = await callApplication(`/api/v1/liff/${audience}/display-preferences`, {
      method: init.method,
      headers: {
        "X-Liff-ID-Token": request.headers.get("X-Liff-ID-Token") ?? "",
        ...(init.body ? { "Content-Type": "application/json" } : {}),
      },
      body: init.body,
    });
    return NextResponse.json(result);
  } catch (error) {
    const status = error instanceof ApplicationError ? error.status : 503;
    return NextResponse.json({ detail: "preferences request failed" }, { status });
  }
}

export async function GET(
  request: Request,
  { params }: { params: Promise<{ audience: string }> },
) {
  const { audience } = await params;
  return forward(request, audience, { method: "GET" });
}

export async function PUT(
  request: Request,
  { params }: { params: Promise<{ audience: string }> },
) {
  const { audience } = await params;
  return forward(request, audience, { method: "PUT", body: await request.text() });
}
