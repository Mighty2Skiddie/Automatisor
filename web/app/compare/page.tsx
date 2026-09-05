import type { Metadata } from "next";
import Link from "next/link";

import CompareView from "@/components/CompareView";

export const metadata: Metadata = {
  title: "Compare — Sector Analyst",
  description:
    "One question, one sector, three analyst lenses. Identical database rows, " +
    "different conclusions.",
};

export default function ComparePage() {
  return (
    <div className="min-h-screen">
      <header className="flex h-14 items-center justify-between border-b border-rule bg-surface px-4 min-[720px]:px-6">
        <span className="text-sm font-semibold">Sector Analyst · Compare</span>
        <Link
          href="/"
          className="rounded border border-rule px-2 py-1 text-xs font-medium text-slate hover:bg-field"
        >
          Back to the desk
        </Link>
      </header>
      <main className="p-4 min-[720px]:p-6">
        <CompareView />
      </main>
    </div>
  );
}
