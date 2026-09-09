import { NextResponse } from "next/server";

import { callApplicationRaw } from "@/lib/api";

const TYPES = new Set(["quote", "service_report"]);

const DOCX =
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

/** A starter template as a Word file the phone's own browser can save.
 *
 *  The same shape as `/api/guide/[audience]` and for the same reason: no
 *  session, because there is no tenant data in it (it is the manual, in
 *  Word), and because the LINE in-app browser can only save a file when
 *  the URL is one it can open with no header attached — the templates
 *  page opens this through `openExternal`.
 *
 *  Not the `/api/phase2` proxy: that one parses every response as JSON,
 *  which is exactly the failure `callApplicationRaw` exists to avoid. */
export async function GET(
  _request: Request,
  { params }: { params: Promise<{ type: string }> },
) {
  const { type } = await params;
  if (!TYPES.has(type)) {
    return NextResponse.json({ detail: "unknown template type" }, { status: 404 });
  }
  try {
    const upstream = await callApplicationRaw(
      `/api/v1/document-template-samples/${type}?format=docx`,
      { method: "GET" },
    );
    if (!upstream.ok) {
      return NextResponse.json(
        { detail: "sample template unavailable" },
        { status: upstream.status },
      );
    }
    return new NextResponse(upstream.body, {
      status: 200,
      headers: {
        "Content-Type": upstream.headers.get("Content-Type") ?? DOCX,
        "Content-Disposition":
          upstream.headers.get("Content-Disposition") ??
          `attachment; filename="chann-template-${type}.docx"`,
        "Cache-Control": "public, max-age=3600",
      },
    });
  } catch {
    return NextResponse.json(
      { detail: "sample template unavailable" },
      { status: 503 },
    );
  }
}
