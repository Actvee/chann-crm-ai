import { adminForward } from "@/lib/admin-proxy";

/** Member role from the platform console (round 18). */
export async function POST(request: Request, { params }: { params: Promise<{ id: string; uid: string }> }) {
  const { id, uid } = await params;
  return adminForward(`/api/v1/platform/tenants/${id}/members/${encodeURIComponent(uid)}/role`, { method: "PATCH", body: await request.text() });
}
