import { NextResponse } from "next/server";

import { ApplicationError, callApplication } from "@/lib/api";

const AUDIENCES = new Set(["customer", "sales", "technician"]);

/** The illustrated how-to for one OA, from the Application's single source. */
export async function GET(
  request: Request,
  { params }: { params: Promise<{ audience: string }> },
) {
  const { audience } = await params;
  if (!AUDIENCES.has(audience)) {
    return NextResponse.json({ detail: "unknown LIFF audience" }, { status: 404 });
  }
  const lang = new URL(request.url).searchParams.get("lang") === "en" ? "en" : "th";
  try {
    const guide = await callApplication(`/api/v1/liff/${audience}/guide?lang=${lang}`, {
      headers: { "X-Liff-ID-Token": request.headers.get("X-Liff-ID-Token") ?? "" },
    });
    return NextResponse.json(guide);
  } catch (error) {
    const status = error instanceof ApplicationError ? error.status : 503;
    return NextResponse.json({ detail: "guide request failed" }, { status });
  }
}
