import { NextResponse } from "next/server";

import { ApplicationError, callApplicationRaw, callApplicationResponse } from "@/lib/api";

type Context = { params: Promise<{ path: string[] }> };

/** Paths whose responses are files rather than JSON. */
function isDocumentPath(path: string[]): boolean {
  const last = path[path.length - 1] ?? "";
  return (
    last === "document" || last === "pdf" || path[path.length - 2] === "documents"
  );
}

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

  // Documents come back as PDF bytes. Parsing those as JSON throws, the
  // catch below turns it into a 503, and the person is told the server is
  // unavailable while holding a request that succeeded — so anything that
  // is not JSON is passed through untouched.
  if (method === "GET" && isDocumentPath(path)) {
    try {
      const upstream = await callApplicationRaw(apiPath, { method, headers });
      if (!upstream.ok) {
        return NextResponse.json(
          { detail: "document unavailable" }, { status: upstream.status },
        );
      }
      return new NextResponse(upstream.body, {
        status: upstream.status,
        headers: {
          "Content-Type":
            upstream.headers.get("Content-Type") ?? "application/octet-stream",
          "Content-Disposition":
            upstream.headers.get("Content-Disposition") ?? "inline",
        },
      });
    } catch {
      return NextResponse.json({ detail: "document unavailable" }, { status: 503 });
    }
  }

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
