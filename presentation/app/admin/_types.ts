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
  status: "trial" | "active" | "suspended" | "deleted" | string;
  /** When the subscription (trial or paid) ends — round 18 renamed it
   *  from trial_expires_at because an active shop has a deadline too. */
  expires_at: string | null;
  /** Set while the company is soft-deleted (status "deleted"). */
  deleted_at?: string | null;
  created_at: string | null;
  owner_chann_uid: string | null;
  owner_name: string | null;
  members: number;
  customers: number;
  tickets: number;
  open_tickets: number;
  deals: number;
  last_activity_at: string | null;
  /** Round 21D — the shop's plan (the Data tier backfills "pro"). */
  plan_code?: string;
  plan?: { code: string; label: string; limits: { members: number | null; ai_reports_per_month: number } } | null;
};

/** Round 21D — what moving to one other plan would lock, and whether the
 *  Data tier would refuse it (more active members than its limit). */
export type PlanPreview = {
  plan: string;
  label: string;
  locks: { feature: string; counts: Record<string, number> }[];
  members: number;
  limit: number | null;
  refused: boolean;
  inactivate: number;
};

export type TenantMember = {
  chann_uid: string;
  role: string;
  /** "sales" | "technician" — which OA the row is for. */
  channel?: string;
  status: string;
  display_name: string | null;
  joined_at: string | null;
};

export type TenantDetail = TenantSummary & {
  legal_name: string | null;
  company_phone: string | null;
  company_email: string | null;
  company_address: string | null;
  tax_id: string | null;
  /** The operator's own notes (platform-only; never shown to the tenant). */
  admin_notes?: string | null;
  /** Made-to-order (AI) charts this shop may have per calendar month, and
   *  how many it has had this month. null = the system default. */
  ai_chart_quota?: number | string | null;
  ai_chart_used?: number | null;
  ai_chart_month?: string | null;
  /** Round 21D — this month's use against the plan's limits. */
  usage?: { members: number; members_limit: number | null; ai_reports_used: number; ai_reports_allowance: number } | null;
  /** Round 21D — every other plan's preview; null when it could not be read. */
  plan_preview?: { current: string; previews: Record<string, PlanPreview> } | null;
  members_detail: TenantMember[];
};

/** The fields an operator may edit on a tenant (18.1) — mirrors the
 *  Application tier's PATCH /platform/tenants/{id}. */
export type TenantEditFields = {
  company_name: string;
  legal_name: string;
  company_phone: string;
  company_email: string;
  company_address: string;
  tax_id: string;
  admin_notes: string;
  /** YYYY-MM-DD in Bangkok, or "" for no deadline. */
  expires_at: string;
  status: "trial" | "active" | "suspended" | "deleted" | string;
  /** "" means back to the plan's own number. */
  ai_chart_quota: string;
  /** Round 21D — starter | pro | enterprise | enterprise_plus. */
  plan_code: string;
};

/** Is the subscription's end already behind us? */
export function isExpired(value: string | null | undefined): boolean {
  if (!value) return false;
  const t = new Date(value).getTime();
  return !Number.isNaN(t) && t < Date.now();
}

/** An ISO instant → the Bangkok calendar day for a <input type="date">. */
export function bangkokDay(value: string | null | undefined): string {
  if (!value) return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString("sv-SE", { timeZone: "Asia/Bangkok" });
}

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
