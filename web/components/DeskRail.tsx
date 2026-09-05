"use client";

import type { ChangeEvent } from "react";

import { MISSING, fieldLabel } from "@/lib/format";
import type { PersonaInfo, SectorInfo } from "@/lib/types";
import { usePersona } from "./PersonaProvider";

/**
 * The 260px desk rail: who is reading, and what they are reading over.
 *
 * The rail carries one of the three permitted accent placements — the active persona
 * item — and it is the only place the persona's `priority_fields` are shown. That
 * chip row is load-bearing: it is the difference between claiming the persona changes
 * the reasoning and showing which columns of the row this lens reaches for first.
 *
 * Below 720px the whole rail collapses to two selects (spec §2 layout), because a
 * fixed-width column of choices is not a phone layout.
 */

/** Three items, three keyboard shortcuts. Beyond that the hint would be a lie. */
const SHORTCUT_LIMIT = 3;

/**
 * The same words wherever a list comes back empty.
 *
 * A select with no options has to say why it is empty, or the reader cannot tell a
 * still-loading rail from a dataset with nothing in it.
 */
function emptyNote(error: string | null, noun: string): string {
  return error ? "Registry unavailable." : `No ${noun} configured.`;
}

function PriorityFieldChips({ persona }: { persona: PersonaInfo }) {
  return (
    <ul className="flex flex-wrap gap-1" aria-label={`Fields ${persona.name} reads first`}>
      {persona.priority_fields.map((field) => (
        // Interface chrome, so the sans face: spec §2 reserves mono for figures that
        // have to align in a column, and a field name is a label, not a figure.
        <li
          key={field}
          className="rounded-sm border border-rule bg-field px-1.5 py-0.5 text-xs leading-tight text-slate"
        >
          {fieldLabel(field)}
        </li>
      ))}
    </ul>
  );
}

/** Static grey blocks, not a shimmer: the rail reserves its space and stays quiet. */
function RailSkeleton({ rows }: { rows: number }) {
  return (
    <ul aria-hidden="true" className="space-y-1 px-3">
      {Array.from({ length: rows }, (_, index) => (
        <li key={index} className="h-11 rounded-sm bg-field" />
      ))}
    </ul>
  );
}

export function DeskRail() {
  const {
    personas,
    sectors,
    persona,
    sector,
    setPersona,
    setSector,
    loading,
    error,
  } = usePersona();

  const activePersona: PersonaInfo | undefined = personas.find(
    (entry) => entry.key === persona,
  );
  // Until the registry lands there is no count to state. Summing an empty list gives
  // 0, and "0 companies" is a claim about the dataset rather than about the fetch —
  // the same conflation of absence with zero the evidence panel exists to avoid.
  const hasRegistry = sectors.length > 0;
  const totalCompanies = sectors.reduce(
    (sum, entry) => sum + entry.company_count,
    0,
  );

  function onPersonaSelect(event: ChangeEvent<HTMLSelectElement>): void {
    // Resolved through the registry rather than cast, so the value that reaches
    // state is always a key the API actually serves.
    const next = personas.find((entry) => entry.key === event.target.value);
    if (next) setPersona(next.key);
  }

  function onSectorSelect(event: ChangeEvent<HTMLSelectElement>): void {
    const next = sectors.find((entry) => entry.key === event.target.value);
    if (next) setSector(next.key);
  }

  return (
    <aside
      aria-label="Desk"
      aria-busy={loading}
      className="w-full shrink-0 border-b border-rule bg-surface min-[720px]:w-[260px] min-[720px]:border-r min-[720px]:border-b-0"
    >
      {/* Compact rail: below 720px the two lists become two selects. */}
      <div className="min-[720px]:hidden">
        <div className="grid grid-cols-2 gap-3 px-4 py-3">
          <div>
            <label
              htmlFor="desk-persona"
              className="mb-1 block text-xs text-slate"
            >
              Persona
            </label>
            <select
              id="desk-persona"
              value={persona}
              onChange={onPersonaSelect}
              disabled={personas.length === 0}
              className="w-full rounded-sm border border-rule bg-surface px-2 py-1.5 text-sm text-ink disabled:text-slate"
            >
              {personas.map((entry) => (
                <option key={entry.key} value={entry.key}>
                  {entry.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label
              htmlFor="desk-sector"
              className="mb-1 block text-xs text-slate"
            >
              Sector
            </label>
            <select
              id="desk-sector"
              value={sector}
              onChange={onSectorSelect}
              disabled={sectors.length === 0}
              className="w-full rounded-sm border border-rule bg-surface px-2 py-1.5 text-sm text-ink disabled:text-slate"
            >
              {sectors.map((entry) => (
                <option key={entry.key} value={entry.key}>
                  {entry.label}
                </option>
              ))}
            </select>
          </div>
        </div>
        {activePersona ? (
          <div className="space-y-2 px-4 pb-3">
            <p className="text-xs leading-snug text-slate">
              {activePersona.lens}
            </p>
            <PriorityFieldChips persona={activePersona} />
          </div>
        ) : null}
        {/* Two disabled, empty selects with no caption are indistinguishable from a
            broken build. The narrow rail owes the reader the same sentence the wide
            one gives. */}
        {!loading && personas.length === 0 ? (
          <p className="px-4 pb-3 text-xs text-slate">
            {emptyNote(error, "personas")}
          </p>
        ) : null}
      </div>

      {/* Full rail. */}
      <div className="hidden min-[720px]:flex min-[720px]:h-full min-[720px]:flex-col">
        <section aria-labelledby="rail-persona-heading" className="pt-4">
          <h2
            id="rail-persona-heading"
            className="px-3 pb-2 text-xs font-medium text-slate"
          >
            Persona
          </h2>
          {personas.length === 0 ? (
            loading ? (
              <RailSkeleton rows={3} />
            ) : (
              <p className="px-3 pb-2 text-xs text-slate">
                {emptyNote(error, "personas")}
              </p>
            )
          ) : (
            <ul>
              {personas.map((entry: PersonaInfo, index: number) => {
                const active = entry.key === persona;
                return (
                  <li key={entry.key}>
                    <button
                      type="button"
                      aria-pressed={active}
                      onClick={() => setPersona(entry.key)}
                      className={`accent-transition block w-full border-l-[3px] px-3 py-2.5 text-left ${
                        active
                          ? "bg-field"
                          : "border-transparent hover:bg-field/60"
                      }`}
                      style={
                        active ? { borderLeftColor: "var(--accent)" } : undefined
                      }
                    >
                      <span className="flex items-baseline justify-between gap-2">
                        <span
                          className="accent-transition text-sm font-semibold"
                          style={active ? { color: "var(--accent)" } : undefined}
                        >
                          {entry.name}
                        </span>
                        {index < SHORTCUT_LIMIT ? (
                          <kbd className="figure text-xs font-normal text-slate">
                            {index + 1}
                          </kbd>
                        ) : null}
                      </span>
                      <span className="mt-0.5 block text-xs leading-snug text-slate">
                        {entry.lens}
                      </span>
                    </button>
                    {/* Attached to the active persona, not floating below the list:
                        the point is that *this* lens reads *these* fields first. */}
                    {active ? (
                      <div className="border-l-[3px] border-transparent bg-field px-3 pb-2.5">
                        <p className="pb-1 text-xs text-slate">Reads first</p>
                        <PriorityFieldChips persona={entry} />
                      </div>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        <section aria-labelledby="rail-sector-heading" className="pt-5">
          <h2
            id="rail-sector-heading"
            className="px-3 pb-2 text-xs font-medium text-slate"
          >
            Sector
          </h2>
          {sectors.length === 0 ? (
            loading ? (
              <RailSkeleton rows={4} />
            ) : (
              <p className="px-3 text-xs text-slate">
                {emptyNote(error, "sectors")}
              </p>
            )
          ) : (
            <ul>
              {sectors.map((entry: SectorInfo) => {
                const active = entry.key === sector;
                return (
                  <li key={entry.key}>
                    <button
                      type="button"
                      aria-pressed={active}
                      onClick={() => setSector(entry.key)}
                      title={entry.description}
                      // Deliberately not the accent: the spec allows it in exactly
                      // three places, and the sector is not one of them.
                      className={`flex w-full items-baseline justify-between gap-2 border-l-[3px] px-3 py-2 text-left text-sm ${
                        active
                          ? "border-ink bg-field font-semibold text-ink"
                          : "border-transparent text-slate hover:bg-field/60"
                      }`}
                    >
                      <span>{entry.label}</span>
                      <span className="figure text-xs text-slate">
                        {entry.company_count}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        <div className="mt-auto border-t border-rule px-3 py-3 text-xs text-slate">
          <p>
            <span className="figure text-ink">
              {hasRegistry ? totalCompanies : MISSING}
            </span>{" "}
            {hasRegistry && totalCompanies === 1 ? "company" : "companies"}
          </p>
          <p>
            <span className="figure text-ink">
              {hasRegistry ? sectors.length : MISSING}
            </span>{" "}
            {sectors.length === 1 ? "sector" : "sectors"}
          </p>
        </div>
      </div>
    </aside>
  );
}

export default DeskRail;
