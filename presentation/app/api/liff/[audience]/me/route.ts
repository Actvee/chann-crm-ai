import { NextResponse } from "next/server";

import { ApplicationError, callApplication } from "@/lib/api";

const AUDIENCES = new Set(["customer", "sales", "technician"]);

export async function GET(
  request: Request,
  { params }: { params: Promise<{ audience: string }> },
) {
  const { audience } = await params;
  if (!AUDIENCES.has(audience)) {
    return NextResponse.json({ detail: "unknown LIFF audience" }, { status: 404 });
  }
  try {
    const profile = await callApplication(`/api/v1/liff/${audience}/me`, {
      headers: { "X-Liff-ID-Token": request.headers.get("X-Liff-ID-Token") ?? "" },
    });
    return NextResponse.json(profile);
  } catch (error) {
    const status = error instanceof ApplicationError ? error.status : 503;
    return NextResponse.json({ detail: "LIFF authentication failed" }, { status });
  }
}
