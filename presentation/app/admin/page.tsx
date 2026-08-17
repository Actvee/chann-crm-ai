import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { callApplication } from "@/lib/api";

type AdminProfile = { username: string; scope: string };

export default async function AdminDashboard() {
  const token = (await cookies()).get("chann_admin_session")?.value;
  if (!token) redirect("/admin/login");

  let profile: AdminProfile;
  try {
    profile = await callApplication<AdminProfile>("/api/v1/platform/me", {
      headers: { Authorization: `Bearer ${token}` },
    });
  } catch {
    redirect("/admin/login");
  }

  return (
    <main style={{ padding: 32 }}>
      <h1>Platform Admin Dashboard</h1>
      <p>Signed in as {profile.username}</p>
      <p>Permission: {profile.scope}</p>
      <p>Phase 1 dashboard shell</p>
    </main>
  );
}
