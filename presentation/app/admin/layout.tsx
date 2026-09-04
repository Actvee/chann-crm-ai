import type { ReactNode } from "react";
import { cookies } from "next/headers";

import { ADMIN } from "@/lib/admin-copy";
import { ApplicationError, callApplication } from "@/lib/api";

import "./admin.css";
import { ADMIN_COOKIE, type AdminProfile } from "./_server";
import { AdminNav } from "./_nav";

export const metadata = { title: "Chann Platform Admin" };

/** Phase 18 — the operator's console. Signed out (no cookie, or a dead
 *  session) renders the bare page so /admin/login stands on its own. */
export default async function AdminLayout({ children }: { children: ReactNode }) {
  const token = (await cookies()).get(ADMIN_COOKIE)?.value;
  let profile: AdminProfile | null = null;
  if (token) {
    try {
      profile = await callApplication<AdminProfile>("/api/v1/platform/me", {
        headers: { Authorization: `Bearer ${token}` },
      });
    } catch (error) {
      if (!(error instanceof ApplicationError)) throw error;
      profile = null;
    }
  }
  return (
    <>
      <link
        href="https://fonts.googleapis.com/css2?family=Fira+Sans:wght@400;500;600&family=Fira+Code:wght@400;500&display=optional"
        rel="stylesheet"
      />
      {profile ? (
        <div className="pa">
          <aside className="pa-rail">
            <a className="pa-brand" href="/admin">
              <span className="pa-brand-mark" aria-hidden="true">C</span>
              <span className="pa-brand-name">
                {ADMIN.brand}
                <span className="pa-brand-sub">{ADMIN.brandSub}</span>
              </span>
            </a>
            <AdminNav />
            <div className="pa-rail-foot">
              <span>
                {ADMIN.signedInAs} <strong>{profile.username}</strong>
              </span>
              <form action="/api/admin/logout" method="post">
                <button type="submit" className="pa-btn pa-btn-sm">{ADMIN.logout}</button>
              </form>
            </div>
          </aside>
          <main className="pa-main">{children}</main>
        </div>
      ) : (
        <div className="pa pa-login">{children}</div>
      )}
    </>
  );
}
