import { NextResponse } from "next/server";

import { callApplicationRaw } from "@/lib/api";

const AUDIENCES = new Set(["customer", "sales", "technician"]);

/** The guide as a file the phone's own browser can open: `?format=html`
 *  (shown inline, save/share from there) or `?format=md` (downloaded).
 *  No session: it is the manual. */
export async function GET(
  request: Request,
  { params }: { params: Promise<{ audience: string }> },
) {
  const { audience } = await params;
  if (!AUDIENCES.has(audience)) {
    return NextResponse.json({ detail: "unknown LIFF audience" }, { status: 404 });
  }
  const format = new URL(request.url).searchParams.get("format") === "md" ? "md" : "html";
  try {
    const upstream = await callApplicationRaw(`/api/v1/guides/${audience}/file?format=${format}`, { method: "GET" });
    if (!upstream.ok) {
      return NextResponse.json({ detail: "guide file unavailable" }, { status: upstream.status });
    }
    return new NextResponse(upstream.body, {
      status: 200,
      headers: {
        "Content-Type": upstream.headers.get("Content-Type") ?? "text/html; charset=utf-8",
        "Content-Disposition":
          upstream.headers.get("Content-Disposition") ??
          (format === "md" ? `attachment; filename="guide-${audience}.md"` : "inline"),
        "Cache-Control": "public, max-age=300",
      },
    });
  } catch {
    return NextResponse.json({ detail: "guide file unavailable" }, { status: 503 });
  }
}
