import { NextResponse } from "next/server";

import { ApplicationError, callApplication } from "@/lib/api";

const AUDIENCES = new Set(["customer", "sales", "technician"]);

/** PDPA consent (16.5): what the person accepted, or accept the current version. */
async function forward(request: Request, audience: string, method: "GET" | "PUT") {
  if (!AUDIENCES.has(audience)) {
    return NextResponse.json({ detail: "unknown LIFF audience" }, { status: 404 });
  }
  try {
    const result = await callApplication(`/api/v1/liff/${audience}/consent`, {
      method,
      headers: { "X-Liff-ID-Token": request.headers.get("X-Liff-ID-Token") ?? "" },
    });
    return NextResponse.json(result);
  } catch (error) {
    const status = error instanceof ApplicationError ? error.status : 503;
    return NextResponse.json({ detail: "consent request failed" }, { status });
  }
}

export async function GET(request: Request, { params }: { params: Promise<{ audience: string }> }) {
  const { audience } = await params;
  return forward(request, audience, "GET");
}

export async function PUT(request: Request, { params }: { params: Promise<{ audience: string }> }) {
  const { audience } = await params;
  return forward(request, audience, "PUT");
}
