import { adminForward } from "@/lib/admin-proxy";

/** Suspend or reopen a tenant (18.1). */
export async function POST(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return adminForward(`/api/v1/platform/tenants/${id}`, { method: "PATCH", body: await request.text() });
}
