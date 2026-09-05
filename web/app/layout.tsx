import type { Metadata } from "next";
import { IBM_Plex_Mono, Newsreader, Public_Sans } from "next/font/google";

import { PersonaProvider } from "@/components/PersonaProvider";

import "./globals.css";

/*
 * Three families, each doing one job (spec §2):
 *   Newsreader   the analyst's prose — a serif makes a multi-paragraph note readable
 *                rather than chat-bubble skimmable
 *   Public Sans  every piece of interface chrome
 *   IBM Plex Mono  numbers only, so columns of figures align
 *
 * Loaded through next/font so they are self-hosted and cause no layout shift, which
 * the spec's quality floor requires.
 */
const newsreader = Newsreader({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-newsreader",
});

const publicSans = Public_Sans({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-public-sans",
});

const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  display: "swap",
  variable: "--font-plex-mono",
});

export const metadata: Metadata = {
  title: "Sector Analyst",
  description:
    "One agent, three analyst personas, four sectors. Every figure comes from a live " +
    "database query over MCP.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    // data-persona is overwritten by PersonaProvider on selection; seeding it here
    // means the accent is correct on first paint instead of flashing a default.
    <html
      lang="en"
      data-persona="mf_analyst"
      className={`${newsreader.variable} ${publicSans.variable} ${plexMono.variable}`}
    >
      <body className="bg-field text-ink antialiased">
        <PersonaProvider>{children}</PersonaProvider>
      </body>
    </html>
  );
}
