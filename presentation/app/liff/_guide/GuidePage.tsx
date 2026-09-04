"use client";

import { useCallback, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { AppShell } from "../sales/_components";
import { Audience, initLiffSession, openExternal } from "../_shared";

type Step = {
  key: string;
  title: string;
  body: string;
  example?: string | null;
  image_url?: string | null;
  image_slot: string;
};
type Guide = { title: string; intro: string; steps: Step[] };

/**
 * The illustrated how-to, one page per OA, from the same source as chat's
 * "วิธีใช้" (services/guides.py via /api/v1/liff/{audience}/guide). Each
 * step has an image slot; a slot with no URL yet renders nothing rather
 * than a broken frame — the owner fills the map as pictures are made.
 */
export default function GuidePage({ liffId, audience }: { liffId: string; audience: Audience }) {
  const { t, locale } = useLanguage();
  const [guide, setGuide] = useState<Guide | null>(null);
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();

  const onReady = useCallback(async () => {
    try {
      const session = await initLiffSession(liffId, audience);
      if (!session.token) return;
      const response = await fetch(`/api/liff/${audience}/guide?lang=${locale}`, {
        headers: { "X-Liff-ID-Token": session.token },
      });
      if (!response.ok) throw new Error(String(response.status));
      setGuide((await response.json()) as Guide);
      setStatus("");
      setTone(undefined);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : t.dashboard.openFailed);
      setTone("error");
    }
  }, [audience, liffId, locale, t]);

  /** The handout as a file (owner, 4 Sep). Opened in the phone's own
   *  browser: inside the LINE webview a blob download did nothing, and a
   *  plain URL the outside browser can show, save and share does. */
  function openFile(format: "md" | "html") {
    openExternal(`${window.location.origin}/api/guide/${audience}?format=${format}`);
  }

  return (
    <div data-theme={audience}>
      <AppShell
        title={guide?.title ?? t.dashboard.guide.title}
        back={`/liff/${audience}`}
        nav={false}
        liffId={liffId}
        onReady={() => void onReady()}
        onSdkError={() => {
          setStatus(t.dashboard.openFailed);
          setTone("error");
        }}
        status={status}
        statusTone={tone}
      >
        {guide && (
          <>
            <p className="page-intro">{guide.intro}</p>
            <div className="actions" style={{ margin: "0 0 14px" }}>
              <button type="button" className="btn" data-variant="primary" onClick={() => openFile("html")}>
                {t.dashboard.guide.downloadHtml}
              </button>
              <button type="button" className="btn" onClick={() => openFile("md")}>
                {t.dashboard.guide.downloadMd}
              </button>
            </div>
            <p className="card-meta" style={{ margin: "0 0 16px" }}>{t.dashboard.guide.downloadHint}</p>
            <ol className="guide">
              {guide.steps.map((step, index) => (
                <li key={step.key} className="section guide-step">
                  <h2>
                    <span className="guide-no">{index + 1}</span> {step.title}
                  </h2>
                  {step.image_url ? (
                    <img
                      className="guide-image"
                      src={step.image_url.startsWith("/") ? `/api/guide-image/${step.image_slot}` : step.image_url}
                      alt={step.title}
                    />
                  ) : null}
                  <p>{step.body}</p>
                  {step.example && (
                    <p className="card-meta">
                      {t.dashboard.guide.type}: <code>{step.example}</code>
                    </p>
                  )}
                </li>
              ))}
            </ol>
          </>
        )}
      </AppShell>
    </div>
  );
}
