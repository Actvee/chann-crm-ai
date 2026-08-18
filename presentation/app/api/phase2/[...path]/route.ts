import { NextResponse } from "next/server";

import { ApplicationError, callApplicationResponse } from "@/lib/api";

type Context = { params: Promise<{ path: string[] }> };

async function proxy(request: Request, context: Context) {
  const { path } = await context.params;
  const method = request.method.toUpperCase();
  const incoming = new URL(request.url);
  const apiPath = `/api/v1/${path.map(encodeURIComponent).join("/")}${incoming.search}`;
  const headers = {
    "X-Liff-ID-Token": request.headers.get("X-Liff-ID-Token") ?? "",
    "X-Liff-Audience": request.headers.get("X-Liff-Audience") ?? "sales",
    "X-License-Id": request.headers.get("X-License-Id") ?? "",
  };
  const body = method === "GET" || method === "DELETE" ? undefined : await request.text();
  try {
    const result = await callApplicationResponse<unknown>(apiPath, { method, headers, body });
    return result.status === 204
      ? new NextResponse(null, { status: result.status })
      : NextResponse.json(result.data, { status: result.status });
  } catch (error) {
    const status = error instanceof ApplicationError ? error.status : 503;
    return NextResponse.json({ detail: "Phase 2 request failed" }, { status });
  }
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
export const PUT = proxy;
export const DELETE = proxy;
