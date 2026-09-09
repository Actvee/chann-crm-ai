import "./globals.css";

import { LanguageProvider } from "@/lib/i18n/LanguageProvider";

export const metadata = {
  title: "Chann CRM AI",
  description: "Multi-tenant CRM and field service platform on LINE",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  // lang is set to the default here and updated client-side by LanguageProvider
  // once the stored preference is known — see the hydration note in that file.
  return (
    <html lang="th">
      <head>
        {/* Thai text set in a Latin-first system font gets mismatched
            line heights and clipped upper vowels. IBM Plex Sans Thai is
            the face this project's own progress report already uses, so
            the app and its reporting read as one product. */}
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Thai:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=optional"
          rel="stylesheet"
        />
        <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
        {/* The left rail's collapsed state, applied before the first paint.
            Reading it in React instead would draw the rail wide and then
            snap it shut on every load — the flash the owner would see on
            every page. Wrapped in try/catch: storage throws outright in
            some in-app browsers, and an expanded rail is the right thing
            to fall back to because nothing is hidden in it. */}
        <script
          dangerouslySetInnerHTML={{
            __html:
              'try{document.documentElement.dataset.nav=' +
              'localStorage.getItem("chann.nav.collapsed")==="1"?"collapsed":"expanded"}catch(e){}',
          }}
        />
      </head>
      <body>
        <LanguageProvider>{children}</LanguageProvider>
      </body>
    </html>
  );
}
