"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useLanguage } from "@/lib/i18n/LanguageProvider";

import { AppShell } from "../sales/_components";
import { Audience, initLiffSession } from "../_shared";

/**
 * Draw a signature once; it is kept against the person (13.5) and
 * printed on every service report they approve. A finger on a phone is
 * the input, so the canvas fills the width and the stroke is thick.
 */
export default function SignaturePage({ liffId, audience }: { liffId: string; audience: Audience }) {
  const { t } = useLanguage();
  const copy = t.dashboard.signature;
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const drawing = useRef(false);
  const [token, setToken] = useState("");
  const [current, setCurrent] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(t.dashboard.opening);
  const [tone, setTone] = useState<"ok" | "error" | undefined>();

  const say = useCallback((message: string, kind?: "ok" | "error") => {
    setStatus(message);
    setTone(kind);
  }, []);

  const onReady = useCallback(async () => {
    try {
      const session = await initLiffSession(liffId, audience);
      if (!session.token) return;
      setToken(session.token);
      const response = await fetch(`/api/liff/${audience}/signature`, {
        headers: { "X-Liff-ID-Token": session.token },
      });
      if (response.ok) {
        const body = (await response.json()) as { url?: string | null };
        setCurrent(body.url ?? null);
      }
      say("");
    } catch (error) {
      say(error instanceof Error ? error.message : t.dashboard.openFailed, "error");
    }
  }, [audience, liffId, say, t]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ratio = window.devicePixelRatio || 1;
    canvas.width = canvas.clientWidth * ratio;
    canvas.height = 220 * ratio;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.scale(ratio, ratio);
    ctx.lineWidth = 3;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.strokeStyle = "#111";
  }, []);

  function point(e: React.PointerEvent<HTMLCanvasElement>) {
    const rect = e.currentTarget.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }

  function start(e: React.PointerEvent<HTMLCanvasElement>) {
    const ctx = canvasRef.current?.getContext("2d");
    if (!ctx) return;
    drawing.current = true;
    const p = point(e);
    ctx.beginPath();
    ctx.moveTo(p.x, p.y);
    e.currentTarget.setPointerCapture(e.pointerId);
  }

  function move(e: React.PointerEvent<HTMLCanvasElement>) {
    if (!drawing.current) return;
    const ctx = canvasRef.current?.getContext("2d");
    if (!ctx) return;
    const p = point(e);
    ctx.lineTo(p.x, p.y);
    ctx.stroke();
    setDirty(true);
  }

  function end() {
    drawing.current = false;
  }

  function clear() {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    setDirty(false);
  }

  async function save() {
    const canvas = canvasRef.current;
    if (!canvas || !dirty) return;
    setBusy(true);
    try {
      const response = await fetch(`/api/liff/${audience}/signature`, {
        method: "POST",
        headers: { "X-Liff-ID-Token": token, "Content-Type": "application/json" },
        body: JSON.stringify({ image: canvas.toDataURL("image/png") }),
      });
      if (!response.ok) throw new Error(String(response.status));
      const body = (await response.json()) as { url?: string | null };
      setCurrent(body.url ?? null);
      clear();
      say(copy.saved, "ok");
    } catch {
      say(copy.saveFailed, "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div data-theme={audience}>
      <AppShell
        title={copy.title}
        back={`/liff/${audience}`}
        nav={false}
        liffId={liffId}
        onReady={() => void onReady()}
        onSdkError={() => say(t.dashboard.openFailed, "error")}
        status={status}
        statusTone={tone}
      >
        <p className="page-intro">{copy.intro}</p>
        {current && (
          <section className="section">
            <div className="section-head">
              <h2>{copy.current}</h2>
            </div>
            <img className="signature-preview" src={current} alt={copy.current} />
          </section>
        )}
        <section className="section">
          <div className="section-head">
            <h2>{copy.draw}</h2>
          </div>
          <canvas
            ref={canvasRef}
            className="signature-pad"
            onPointerDown={start}
            onPointerMove={move}
            onPointerUp={end}
            onPointerLeave={end}
            onPointerCancel={end}
          />
          <div className="actions">
            <button type="button" className="btn" data-variant="quiet" onClick={clear} disabled={busy}>
              {copy.clear}
            </button>
            <button
              type="button"
              className="btn"
              data-variant="primary"
              onClick={() => void save()}
              disabled={busy || !dirty}
            >
              {busy ? t.dashboard.related.saving : copy.save}
            </button>
          </div>
        </section>
      </AppShell>
    </div>
  );
}
