import { adminForward } from "@/lib/admin-proxy";

/** Edit a tenant: status, trial deadline, shop details (18.1, 7 Sep 2026). */
export async function POST(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return adminForward(`/api/v1/platform/tenants/${id}`, { method: "PATCH", body: await request.text() });
}
