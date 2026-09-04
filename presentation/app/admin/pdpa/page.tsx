import { ADMIN } from "@/lib/admin-copy";

import { adminCall, type PdpaRequest } from "../_server";
import { PdpaQueue } from "./PdpaQueue";

const copy = ADMIN.pdpa;

/** Phase 16.5 admin side, housed in the Phase 18 console: the queue of
 *  data-subject requests, with process/reject for the pending ones. */
export default async function AdminPdpa({
  searchParams,
}: {
  searchParams: Promise<{ status?: string }>;
}) {
  const { status = "" } = await searchParams;
  const query = status ? `?status_filter=${encodeURIComponent(status)}` : "";
  const rows = await adminCall<PdpaRequest[]>(`/api/v1/platform/pdpa/requests${query}`);
  const pending = rows.filter((r) => r.status === "pending").length;

  return (
    <>
      <div className="pa-head">
        <div>
          <h1>{copy.title}</h1>
          <p>{copy.intro}</p>
        </div>
      </div>
      <div className="pa-metrics">
        <div className="pa-metric"><div className="pa-metric-label">{copy.total}</div><div className="pa-metric-value">{rows.length}</div></div>
        <div className="pa-metric"><div className="pa-metric-label">{copy.pending}</div><div className="pa-metric-value">{pending}</div></div>
      </div>
      <form className="pa-filters" method="get" action="/admin/pdpa">
        <label className="pa-field">
          {copy.statusLabel}
          <select name="status" defaultValue={status}>
            <option value="">{copy.anyStatus}</option>
            <option value="pending">{ADMIN.status.pending}</option>
            <option value="completed">{ADMIN.status.completed}</option>
            <option value="rejected">{ADMIN.status.rejected}</option>
          </select>
        </label>
        <button type="submit" className="pa-btn pa-btn-primary">{copy.filter}</button>
      </form>
      <PdpaQueue rows={rows} />
    </>
  );
}
