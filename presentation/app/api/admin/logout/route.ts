import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { ApplicationError, callApplication } from "@/lib/api";
import { ADMIN_COOKIE } from "@/lib/admin-proxy";

/** End the operator's session on the Application tier and drop the cookie. */
export async function POST(request: Request) {
  const token = (await cookies()).get(ADMIN_COOKIE)?.value;
  if (token) {
    try {
      await callApplication("/api/v1/platform/logout", {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
    } catch (error) {
      if (!(error instanceof ApplicationError)) throw error;
    }
  }
  const response = NextResponse.redirect(new URL("/admin/login", request.url), { status: 303 });
  response.cookies.set(ADMIN_COOKIE, "", { httpOnly: true, sameSite: "lax", path: "/", maxAge: 0 });
  return response;
}
