import { adminForward } from "@/lib/admin-proxy";

/** Delete a company (round 18): soft by default, {purge:true} for good.
 *  The browser POSTs (adminCall only POSTs); the Application tier's
 *  route is a DELETE with a query flag. */
export async function POST(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  let purge = false;
  try {
    const body = (await request.json()) as { purge?: unknown } | null;
    purge = body?.purge === true;
  } catch {
    purge = false;
  }
  return adminForward(`/api/v1/platform/tenants/${id}?purge=${purge ? "true" : "false"}`, { method: "DELETE" });
}
