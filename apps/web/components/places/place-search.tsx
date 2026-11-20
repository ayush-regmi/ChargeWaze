"use client";

import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";

import { cn } from "@/lib/utils";
import { fieldClass } from "@/components/ui/field";
import { tryAgainText } from "@/lib/retry-after";

import {
  retrievePlace,
  suggestPlaces,
  type Place,
  type PlaceError,
  type PlaceSuggestion,
} from "./actions";

export const SUGGEST_DEBOUNCE_MS = 250;
export const MIN_QUERY_LENGTH = 3;

/** A Search Box session token (D2): one per typing → pick cycle. */
export function newSessionToken(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  // RFC 4122 v4 from getRandomValues, for older browsers.
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

function errorText({ error: code, retryAfterSeconds }: PlaceError): string {
  if (code === "rate_limited") {
    return `Too many searches in a short time. ${tryAgainText(retryAfterSeconds)}`;
  }
  if (code === "unauthenticated") return "Your session has ended. Sign in again to search.";
  if (code === "not_found") return "That place couldn't be found. Try another result.";
  return "Place search isn't available right now. Please try again.";
}

const KIND_LABELS: Record<PlaceSuggestion["kind"], string> = {
  address: "Address",
  poi: "Place of interest",
  place: "Place",
};

export type PlaceSearchProps = {
  /** Visible label, e.g. "Search near" or "From". */
  label: string;
  placeholder?: string;
  /** Proximity bias: the map centre or the other trip endpoint (D2). */
  near?: { lat: number; lon: number } | null;
  /** Text shown before the user types, e.g. the current place's label. */
  initialValue?: string;
  onSelect: (place: Place) => void;
  className?: string;
};

/**
 * Place search combobox (WAI-ARIA combobox with listbox popup). Debounced
 * 250 ms, at least 3 characters, and one session token per search session
 * that ends when a result is picked (D2, D15). Used by the Charging page and
 * the Trip Planner.
 */
export function PlaceSearch({
  label,
  placeholder = "City, address or place",
  near = null,
  initialValue = "",
  onSelect,
  className,
}: PlaceSearchProps) {
  const id = useId();
  const inputId = `${id}-input`;
  const listId = `${id}-list`;
  const [value, setValue] = useState(initialValue);
  const [items, setItems] = useState<PlaceSuggestion[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<PlaceError | null>(null);
  const [searched, setSearched] = useState(false);

  const sessionRef = useRef<string | null>(null);
  const requestRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const nearRef = useRef(near);
  useEffect(() => {
    nearRef.current = near;
  });

  useEffect(() => () => {
    if (timerRef.current) clearTimeout(timerRef.current);
  }, []);

  const session = () => (sessionRef.current ??= newSessionToken());

  const runSuggest = async (q: string) => {
    const req = ++requestRef.current;
    setLoading(true);
    const result = await suggestPlaces({ q, sessionToken: session(), near: nearRef.current });
    if (req !== requestRef.current) return; // a newer keystroke won
    setLoading(false);
    setSearched(true);
    if ("error" in result) {
      setItems([]);
      setError(result);
      setOpen(true);
      return;
    }
    setError(null);
    setItems(result.items);
    setActive(result.items.length > 0 ? 0 : -1);
    setOpen(true);
  };

  const onChange = (next: string) => {
    setValue(next);
    if (timerRef.current) clearTimeout(timerRef.current);
    const q = next.trim();
    if (q.length < MIN_QUERY_LENGTH) {
      requestRef.current++;
      setItems([]);
      setOpen(false);
      setLoading(false);
      setError(null);
      setSearched(false);
      return;
    }
    timerRef.current = setTimeout(() => void runSuggest(q), SUGGEST_DEBOUNCE_MS);
  };

  // Text typed into the server-rendered input before hydration (slow JS, or a
  // test that types as soon as the page loads) stays in the field, but React
  // never saw an input event for it: hydration keeps the typed value and
  // starts its change tracking from it, so no later onChange would fire and
  // the search would never run. Pick that text up once, after hydration.
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const typed = inputRef.current?.value;
    if (typed !== undefined && typed !== value) onChange(typed);
    // Mount only: later typing goes through onChange as usual.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const choose = async (s: PlaceSuggestion) => {
    // A suggest still waiting on the debounce would start after this pick and
    // win the request-id check, dropping the pick. Cancel it.
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    const req = ++requestRef.current;
    setLoading(true);
    const result = await retrievePlace({ id: s.id, sessionToken: session() });
    if (req !== requestRef.current) return;
    setLoading(false);
    if ("error" in result) {
      setError(result);
      setOpen(true);
      return;
    }
    // The pick ends this session; the next search starts a new one.
    sessionRef.current = null;
    setValue(result.place.name);
    setItems([]);
    setOpen(false);
    setActive(-1);
    setError(null);
    setSearched(false);
    onSelect(result.place);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      if (items.length === 0) return;
      e.preventDefault();
      setOpen(true);
      setActive((i) => (i + 1) % items.length);
    } else if (e.key === "ArrowUp") {
      if (items.length === 0) return;
      e.preventDefault();
      setOpen(true);
      setActive((i) => (i <= 0 ? items.length - 1 : i - 1));
    } else if (e.key === "Enter") {
      if (open && active >= 0 && items[active]) {
        e.preventDefault();
        void choose(items[active]);
      }
    } else if (e.key === "Escape") {
      if (open) {
        e.preventDefault();
        setOpen(false);
        setActive(-1);
      }
    }
  };

  const optionId = (i: number) => `${id}-opt-${i}`;
  const showList = open && items.length > 0;

  return (
    <div className={cn("relative space-y-1", className)}>
      <label htmlFor={inputId} className="text-sm font-medium">
        {label}
      </label>
      <input
        ref={inputRef}
        id={inputId}
        type="text"
        role="combobox"
        autoComplete="off"
        spellCheck={false}
        aria-autocomplete="list"
        aria-expanded={showList}
        aria-controls={listId}
        aria-activedescendant={showList && active >= 0 ? optionId(active) : undefined}
        aria-busy={loading || undefined}
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        onBlur={() => setOpen(false)}
        onFocus={() => items.length > 0 && setOpen(true)}
        className={`${fieldClass} w-full`}
      />
      <ul
        id={listId}
        role="listbox"
        aria-label={`${label} suggestions`}
        hidden={!showList}
        className="absolute z-20 mt-1 max-h-72 w-full overflow-auto rounded-md border border-border bg-background py-1 shadow-lg"
      >
        {items.map((s, i) => (
          <li
            key={s.id}
            id={optionId(i)}
            role="option"
            aria-selected={i === active}
            // mousedown, not click: it runs before the input's blur closes the list.
            onMouseDown={(e) => {
              e.preventDefault();
              void choose(s);
            }}
            onMouseEnter={() => setActive(i)}
            className={cn(
              "cursor-pointer px-3 py-2 text-sm",
              i === active && "bg-muted",
            )}
          >
            <span className="block font-medium">{s.name}</span>
            <span className="block text-xs text-muted-foreground">
              {s.full_address || KIND_LABELS[s.kind]}
            </span>
          </li>
        ))}
      </ul>
      <div aria-live="polite" className="text-xs">
        {loading ? <span className="text-muted-foreground">Searching…</span> : null}
        {!loading && error ? (
          <span role="alert" className="text-destructive">
            {errorText(error)}
          </span>
        ) : null}
        {!loading && !error && searched && open && items.length === 0 ? (
          <span className="text-muted-foreground">No places found.</span>
        ) : null}
      </div>
    </div>
  );
}
