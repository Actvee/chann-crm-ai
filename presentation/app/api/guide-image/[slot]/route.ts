import { NextResponse } from "next/server";

import { callApplicationRaw } from "@/lib/api";

const SLOT = /^[a-z0-9-]{1,40}$/;

/** The illustrated-guide pictures live in the Application image (one
 *  source for chat and the web). The guide page reaches them through this
 *  proxy so the browser never needs the Application host. */
export async function GET(_request: Request, { params }: { params: Promise<{ slot: string }> }) {
  const { slot } = await params;
  if (!SLOT.test(slot)) {
    return NextResponse.json({ detail: "unknown image" }, { status: 404 });
  }
  try {
    const upstream = await callApplicationRaw(`/api/v1/guide/images/${slot}.png`, { method: "GET" });
    if (!upstream.ok) {
      return NextResponse.json({ detail: "unknown image" }, { status: upstream.status });
    }
    const bytes = await upstream.arrayBuffer();
    return new NextResponse(bytes, {
      status: 200,
      headers: { "Content-Type": "image/png", "Cache-Control": "public, max-age=3600" },
    });
  } catch {
    return NextResponse.json({ detail: "guide image unavailable" }, { status: 503 });
  }
}
