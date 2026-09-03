import { NextResponse } from "next/server";

import { ApplicationError, callApplication } from "@/lib/api";

const AUDIENCES = new Set(["customer", "sales", "technician"]);

/** Store which of the person's shops the app acts in on this OA. */
export async function PUT(
  request: Request,
  { params }: { params: Promise<{ audience: string }> },
) {
  const { audience } = await params;
  if (!AUDIENCES.has(audience)) {
    return NextResponse.json({ detail: "unknown LIFF audience" }, { status: 404 });
  }
  try {
    const result = await callApplication(`/api/v1/liff/${audience}/active-shop`, {
      method: "PUT",
      headers: {
        "X-Liff-ID-Token": request.headers.get("X-Liff-ID-Token") ?? "",
        "Content-Type": "application/json",
      },
      body: await request.text(),
    });
    return NextResponse.json(result);
  } catch (error) {
    const status = error instanceof ApplicationError ? error.status : 503;
    return NextResponse.json({ detail: "could not switch shop" }, { status });
  }
}
