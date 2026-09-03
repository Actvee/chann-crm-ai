import { NextResponse } from "next/server";

import { ApplicationError, callApplication, callApplicationRaw } from "@/lib/api";

const AUDIENCES = new Set(["customer", "sales", "technician"]);

/** The illustrated how-to for one OA, from the Application's single
 *  source — as JSON for the page, or as a file (`format=md|html`). */
export async function GET(
  request: Request,
  { params }: { params: Promise<{ audience: string }> },
) {
  const { audience } = await params;
  if (!AUDIENCES.has(audience)) {
    return NextResponse.json({ detail: "unknown LIFF audience" }, { status: 404 });
  }
  const search = new URL(request.url).searchParams;
  const lang = search.get("lang") === "en" ? "en" : "th";
  const format = search.get("format");
  const headers = { "X-Liff-ID-Token": request.headers.get("X-Liff-ID-Token") ?? "" };
  if (format === "md" || format === "html") {
    try {
      const upstream = await callApplicationRaw(
        `/api/v1/liff/${audience}/guide?lang=${lang}&format=${format}`, { method: "GET", headers },
      );
      if (!upstream.ok) {
        return NextResponse.json({ detail: "guide file unavailable" }, { status: upstream.status });
      }
      return new NextResponse(upstream.body, {
        status: 200,
        headers: {
          "Content-Type": upstream.headers.get("Content-Type") ?? "text/plain; charset=utf-8",
          "Content-Disposition":
            upstream.headers.get("Content-Disposition") ?? `attachment; filename="guide-${audience}.${format}"`,
        },
      });
    } catch {
      return NextResponse.json({ detail: "guide file unavailable" }, { status: 503 });
    }
  }
  try {
    const guide = await callApplication(`/api/v1/liff/${audience}/guide?lang=${lang}`, { headers });
    return NextResponse.json(guide);
  } catch (error) {
    const status = error instanceof ApplicationError ? error.status : 503;
    return NextResponse.json({ detail: "guide request failed" }, { status });
  }
}
