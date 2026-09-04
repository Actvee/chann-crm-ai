/** Types and pure formatting shared by the admin pages and their client
 *  components. No server-only imports here: client components may import
 *  this file, but not _server.ts (which pulls in next/headers). */
import { ADMIN } from "@/lib/admin-copy";

export type AdminProfile = { username: string; scope: string };

export type TenantSummary = {
  id: string;
  license_code: string;
  company_name: string;
  company_code: string | null;
  status: "trial" | "active" | "suspended" | string;
  trial_expires_at: string | null;
  created_at: string | null;
  owner_chann_uid: string | null;
  owner_name: string | null;
  members: number;
  customers: number;
  tickets: number;
  open_tickets: number;
  deals: number;
  last_activity_at: string | null;
};

export type TenantMember = {
  chann_uid: string;
  role: string;
  status: string;
  display_name: string | null;
  joined_at: string | null;
};

export type TenantDetail = TenantSummary & {
  legal_name: string | null;
  company_phone: string | null;
  company_email: string | null;
  members_detail: TenantMember[];
};

export type AuditRow = {
  id: string;
  license_id: string | null;
  entity_type: string;
  entity_id: string;
  actor_type: string;
  actor_id: string | null;
  action: string;
  field_changes: Record<string, unknown> | null;
  cross_tenant: boolean;
  created_at: string;
};

export type PdpaRequest = {
  id: string;
  chann_uid: string;
  request_type: string;
  status: string;
  requested_via: string;
  requested_at: string;
  completed_at: string | null;
  rejection_reason: string | null;
  result_json: Record<string, unknown> | null;
};

export function fmtDate(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString("th-TH", { timeZone: "Asia/Bangkok", dateStyle: "medium", timeStyle: "short" });
}

export const STATUS_LABEL: Record<string, string> = ADMIN.status;
