import { adminForward } from "@/lib/admin-proxy";

/** Renew a tenant's subscription by N days (round 18). */
export async function POST(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return adminForward(`/api/v1/platform/tenants/${id}/extend`, { method: "POST", body: await request.text() });
}
