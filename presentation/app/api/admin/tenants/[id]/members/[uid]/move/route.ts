import { adminForward } from "@/lib/admin-proxy";

/** Move a member to another company (round 18). */
export async function POST(request: Request, { params }: { params: Promise<{ id: string; uid: string }> }) {
  const { id, uid } = await params;
  return adminForward(`/api/v1/platform/tenants/${id}/members/${encodeURIComponent(uid)}/move`, { method: "POST", body: await request.text() });
}
