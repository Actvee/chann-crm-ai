"use client";

import { useCallback, useEffect, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { initLiffSession, whenLiffReady, proxyHeaders } from "./_lib";

type Pipeline = {
  by_stage: Record<string, { count: number; value: string }>;
  open_value: string;
  closing_this_month: string;
  overdue_count: number;
  undated_open_count: number;
};

function money(value: string | number | undefined): string {
  const n = Number(value ?? 0);
  return n.toLocaleString("th-TH", { maximumFractionDigits: 0 });
}

/**
 * The numbers a shop owner opens the dashboard for.
 *
 * Sits on the index rather than behind a tile: someone who has to tap
 * through to see how their month is going will mostly not look, and a
 * forecast nobody reads is the same as no forecast.
 *
 * Loads on its own and stays quiet when it fails. The menu below it is
 * the page's actual job, and a summary that cannot load must not take
 * the navigation down with it.
 */
export default function PipelineSummary({ liffId }: { liffId: string }) {
  const { t } = useLanguage();
  const [data, setData] = useState<Pipeline | null>(null);

  const load = useCallback(async () => {
    try {
      await whenLiffReady();
      const session = await initLiffSession(liffId);
      const license = session.memberships[0]?.license_id ?? "";
      if (!session.token || !license) return;
      const response = await fetch(
        `/api/phase2/licenses/${license}/pipeline`,
        { headers: proxyHeaders(session.token, license) },
      );
      if (!response.ok) return;
      setData((await response.json()) as Pipeline);
    } catch {
      // Silent: the menu is what this page is for.
    }
  }, [liffId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!data) return null;

  const open = Number(data.open_value ?? 0);
  const closing = Number(data.closing_this_month ?? 0);
  const openCount =
    (data.by_stage?.new?.count ?? 0) + (data.by_stage?.proposed?.count ?? 0);

  // Nothing in the pipeline: the empty state belongs to the menu, not to
  // a row of zeroes that says the same thing less clearly.
  if (openCount === 0 && open === 0) return null;

  return (
    <section className="pipeline">
      <div className="pipeline-figures">
        <div>
          <span className="pipeline-value">{money(open)}</span>
          <span className="pipeline-label">
            {t.dashboard.pipeline.openValue.replace("{count}", String(openCount))}
          </span>
        </div>
        <div>
          <span className="pipeline-value">{money(closing)}</span>
          <span className="pipeline-label">{t.dashboard.pipeline.closingThisMonth}</span>
        </div>
      </div>

      {/* Two ways the number above can mislead, said plainly rather than
          folded into it: an overdue deal is not a forecast, and a
          forecast that ignores half the pipeline is not one either. */}
      {(data.overdue_count > 0 || data.undated_open_count > 0) && (
        <p className="pipeline-caveats">
          {data.overdue_count > 0 && (
            <span>
              {t.dashboard.pipeline.overdue.replace(
                "{count}", String(data.overdue_count),
              )}
            </span>
          )}
          {data.undated_open_count > 0 && (
            <span>
              {t.dashboard.pipeline.undated.replace(
                "{count}", String(data.undated_open_count),
              )}
            </span>
          )}
        </p>
      )}
    </section>
  );
}
