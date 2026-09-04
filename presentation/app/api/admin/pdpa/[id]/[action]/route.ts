import { NextResponse } from "next/server";

import { adminForward } from "@/lib/admin-proxy";

/** PDPA queue actions (16.5): process or reject a request, or create one
 *  on a person's behalf (`/new/create`). */
export async function POST(request: Request, { params }: { params: Promise<{ id: string; action: string }> }) {
  const { id, action } = await params;
  const body = await request.text();
  if (id === "new" && action === "create") {
    return adminForward("/api/v1/platform/pdpa/requests", { method: "POST", body });
  }
  if (action !== "process" && action !== "reject") {
    return NextResponse.json({ detail: "unknown PDPA action" }, { status: 404 });
  }
  return adminForward(`/api/v1/platform/pdpa/requests/${id}/${action}`, { method: "POST", body });
}
