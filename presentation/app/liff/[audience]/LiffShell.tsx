"use client";

import Script from "next/script";
import { useCallback, useState } from "react";

type Audience = "customer" | "sales" | "technician";
type LiffApi = {
  init(config: { liffId: string; withLoginOnExternalBrowser: boolean }): Promise<void>;
  isLoggedIn(): boolean;
  login(): void;
  getIDToken(): string | null;
};

declare global {
  interface Window {
    liff?: LiffApi;
  }
}

export default function LiffShell({ audience, liffId }: { audience: Audience; liffId: string }) {
  const [status, setStatus] = useState("Starting LIFF…");

  const initialize = useCallback(async () => {
    if (!liffId || !window.liff) {
      setStatus(`NEXT_PUBLIC_LIFF_${audience.toUpperCase()}_ID is REQUIRED_NOT_CONFIGURED`);
      return;
    }
    try {
      await window.liff.init({ liffId, withLoginOnExternalBrowser: true });
      if (!window.liff.isLoggedIn()) {
        window.liff.login();
        return;
      }
      const idToken = window.liff.getIDToken();
      if (!idToken) throw new Error("LIFF did not return an ID token");
      const response = await fetch(`/api/liff/${audience}/me`, {
        headers: { "X-Liff-ID-Token": idToken },
      });
      if (!response.ok) throw new Error(`authentication failed (${response.status})`);
      const profile = (await response.json()) as { sub: string };
      setStatus(`Ready — LINE user ${profile.sub}`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "LIFF initialization failed");
    }
  }, [audience, liffId]);

  return (
    <main style={{ padding: 24 }}>
      <Script
        src="https://static.line-scdn.net/liff/edge/versions/2.29.2/sdk.js"
        strategy="afterInteractive"
        onReady={() => void initialize()}
        onError={() => setStatus("LIFF SDK load failed")}
      />
      <h1>Chann CRM AI — {audience}</h1>
      <p>{status}</p>
      {audience === "sales" && <a href="/liff/sales/roles">Roles &amp; permissions</a>}
    </main>
  );
}
