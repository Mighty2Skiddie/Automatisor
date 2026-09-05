"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { API_URL, ApiError, getPersonas, getSectors } from "@/lib/api";
import type {
  PersonaInfo,
  PersonaKey,
  SectorInfo,
  SectorKey,
} from "@/lib/types";

/**
 * The single source of accent truth.
 *
 * The persona is the identity of the session, not a dropdown value, so it lives in
 * one place and is published to the document as `data-persona`. Every persona-aware
 * element then reads `var(--accent)` from CSS instead of receiving a colour through
 * props — which is why no component in this app ever names a persona colour.
 */

/**
 * Rendered before the registries land, so the rail and header have something
 * coherent to draw. Reconciled against the live registry the moment it arrives: if
 * the API does not serve these keys, the first entry it does serve wins.
 */
const FALLBACK_PERSONA: PersonaKey = "mf_analyst";
const FALLBACK_SECTOR: SectorKey = "tech";

export interface PersonaContextValue {
  /** Persona registry from `GET /v1/personas`, in the API's own order. */
  personas: PersonaInfo[];
  /** Sector registry from `GET /v1/sectors`, in the API's own order. */
  sectors: SectorInfo[];
  persona: PersonaKey;
  sector: SectorKey;
  setPersona: (key: PersonaKey) => void;
  setSector: (key: SectorKey) => void;
  loading: boolean;
  /** A sentence naming what failed and how to fix it, or `null`. */
  error: string | null;
}

const PersonaContext = createContext<PersonaContextValue | null>(null);

/**
 * `useLayoutEffect` on the client, `useEffect` on the server.
 *
 * The accent must be on `<html>` before the browser paints, otherwise the first
 * frame after hydration shows the CSS default accent and the interface visibly
 * flinches. React warns about `useLayoutEffect` during SSR, so it is swapped there —
 * where it would be a no-op regardless.
 */
const useIsomorphicLayoutEffect =
  typeof window === "undefined" ? useEffect : useLayoutEffect;

/**
 * Turn a failed registry fetch into something the user can act on.
 *
 * A bare "Failed to fetch" tells the reviewer nothing; naming the address and the
 * command that starts the service is the difference between a dead screen and a
 * fixable one.
 */
function describeFailure(cause: unknown): string {
  if (cause instanceof ApiError) {
    return `The API at ${API_URL} rejected the registry request (${cause.status}): ${cause.message}`;
  }
  // fetch() rejects with a TypeError only when the request never reached a server — a
  // dead port, a DNS failure or a CORS rejection. That is the one case where naming a
  // start command is honest; a malformed body or a JSON parse failure is a different
  // bug, and telling the reader to start an already-running service wastes their time.
  if (cause instanceof TypeError) {
    return `Data service unreachable at ${API_URL}. Start the API with: uvicorn app.api.main:app --port 8000`;
  }
  const detail = cause instanceof Error ? cause.message : String(cause);
  return `The persona and sector registries could not be read from ${API_URL}: ${detail}`;
}

/** True when the keystroke belongs to whatever the user is typing into. */
function isTextEntry(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
}

export function PersonaProvider({ children }: { children: ReactNode }) {
  const [personas, setPersonas] = useState<PersonaInfo[]>([]);
  const [sectors, setSectors] = useState<SectorInfo[]>([]);
  const [persona, setPersonaState] = useState<PersonaKey>(FALLBACK_PERSONA);
  const [sector, setSectorState] = useState<SectorKey>(FALLBACK_SECTOR);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Guards against applying a resolved fetch after unmount, and against the
    // second run of React's development double-invoke clobbering the first.
    let cancelled = false;

    async function load(): Promise<void> {
      try {
        const [personaList, sectorList] = await Promise.all([
          getPersonas(),
          getSectors(),
        ]);
        if (cancelled) return;

        setPersonas(personaList);
        setSectors(sectorList);

        // The registry, not this file, decides what exists. If the optimistic
        // default is not on offer, fall back to the API's first entry rather than
        // leaving the UI pointing at a key the backend would 422.
        setPersonaState((current) =>
          personaList.some((entry) => entry.key === current)
            ? current
            : (personaList[0]?.key ?? current),
        );
        setSectorState((current) =>
          sectorList.some((entry) => entry.key === current)
            ? current
            : (sectorList[0]?.key ?? current),
        );
        setError(null);
      } catch (cause: unknown) {
        if (cancelled) return;
        // The rail must not render as an empty shell that looks like "no data".
        // Registries stay empty and the page shows ErrorState instead.
        setError(describeFailure(cause));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  useIsomorphicLayoutEffect(() => {
    document.documentElement.dataset.persona = persona;
  }, [persona]);

  useEffect(() => {
    /** Spec §4: `1/2/3` switch persona — but never while the user is typing. */
    function onKeyDown(event: KeyboardEvent): void {
      // Cmd/Ctrl/Alt+digit belongs to the browser (tab switching); leave it alone.
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (isTextEntry(event.target)) return;

      const index = Number.parseInt(event.key, 10) - 1;
      if (!Number.isInteger(index) || index < 0 || index > 2) return;

      const next = personas[index];
      if (!next) return;

      event.preventDefault();
      setPersonaState(next.key);
    }

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [personas]);

  const setPersona = useCallback((key: PersonaKey) => setPersonaState(key), []);
  const setSector = useCallback((key: SectorKey) => setSectorState(key), []);

  const value = useMemo<PersonaContextValue>(
    () => ({
      personas,
      sectors,
      persona,
      sector,
      setPersona,
      setSector,
      loading,
      error,
    }),
    [personas, sectors, persona, sector, setPersona, setSector, loading, error],
  );

  return (
    <PersonaContext.Provider value={value}>{children}</PersonaContext.Provider>
  );
}

export function usePersona(): PersonaContextValue {
  const value = useContext(PersonaContext);
  if (!value) {
    throw new Error("usePersona must be used inside <PersonaProvider>");
  }
  return value;
}

export default PersonaProvider;
