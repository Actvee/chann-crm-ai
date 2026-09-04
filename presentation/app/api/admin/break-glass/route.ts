import { adminForward } from "@/lib/admin-proxy";

/** Break-glass owner transfer (18.4). */
export async function POST(request: Request) {
  return adminForward("/api/v1/platform/break-glass/transfer-owner", { method: "POST", body: await request.text() });
}
