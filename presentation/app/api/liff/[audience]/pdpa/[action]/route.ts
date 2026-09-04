import { NextResponse } from "next/server";

import { ApplicationError, callApplication } from "@/lib/api";

const AUDIENCES = new Set(["customer", "sales", "technician"]);
const ACTIONS = new Set(["export", "erase"]);

/** PDPA rights (16.5): a copy of everything, or erasure — the person's own. */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ audience: string; action: string }> },
) {
  const { audience, action } = await params;
  if (!AUDIENCES.has(audience) || !ACTIONS.has(action)) {
    return NextResponse.json({ detail: "unknown PDPA action" }, { status: 404 });
  }
  try {
    const result = await callApplication(`/api/v1/liff/${audience}/pdpa/${action}`, {
      method: "POST",
      headers: {
        "X-Liff-ID-Token": request.headers.get("X-Liff-ID-Token") ?? "",
        "Content-Type": "application/json",
      },
      body: await request.text(),
    });
    return NextResponse.json(result);
  } catch (error) {
    const status = error instanceof ApplicationError ? error.status : 503;
    return NextResponse.json({ detail: "pdpa request failed" }, { status });
  }
}
