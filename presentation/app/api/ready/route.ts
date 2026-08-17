import { NextResponse } from "next/server";

const BASE = process.env.APPLICATION_BASE_URL ?? "http://localhost:8080";

export async function GET() {
  try {
    const response = await fetch(`${BASE}/ready`, { cache: "no-store" });
    const body = await response.json();
    return NextResponse.json(
      { status: response.ok ? "ready" : "not_ready", application: body },
      { status: response.status },
    );
  } catch {
    return NextResponse.json(
      { status: "not_ready", application: "unreachable" },
      { status: 503 },
    );
  }
}
