import { NextResponse } from "next/server";

import { ApplicationError, callApplication } from "@/lib/api";

type TokenResponse = { access_token: string; token_type: string };

export async function POST(request: Request) {
  try {
    const payload = await request.json();
    const token = await callApplication<TokenResponse>("/api/v1/platform/login", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    const response = NextResponse.json({ ok: true });
    response.cookies.set("chann_admin_session", token.access_token, {
      httpOnly: true,
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
      path: "/",
      maxAge: 86400,
    });
    return response;
  } catch (error) {
    const status = error instanceof ApplicationError ? error.status : 503;
    // 423 carries locked_until; the page tells the operator when to
    // come back instead of "wrong password" (review D2, 6 Sep 2026).
    const detail =
      error instanceof ApplicationError && error.body && typeof error.body === "object"
        ? (error.body as { detail?: unknown }).detail
        : undefined;
    return NextResponse.json({ ok: false, detail }, { status });
  }
}
