import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { ApplicationError, callApplication } from "@/lib/api";

export const ADMIN_COOKIE = "chann_admin_session";

export * from "./_types";
import type { AdminProfile } from "./_types";

/** The signed-in operator's token, or a redirect to the login page. */
export async function adminToken(): Promise<string> {
  const token = (await cookies()).get(ADMIN_COOKIE)?.value;
  if (!token) redirect("/admin/login");
  return token;
}

/** A server-side call to the Application tier as the signed-in operator.
 *  A 401 means the session ended: back to login rather than an error page. */
export async function adminCall<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = await adminToken();
  try {
    return await callApplication<T>(path, {
      ...init,
      headers: { Authorization: `Bearer ${token}`, ...(init.headers ?? {}) },
    });
  } catch (error) {
    if (error instanceof ApplicationError && error.status === 401) redirect("/admin/login");
    throw error;
  }
}
