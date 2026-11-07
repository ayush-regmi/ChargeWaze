import type { ReactNode } from "react";

/**
 * Reusable "phase-N placeholder" card so the Phase 1 app shell can render
 * every nav route without implementing them yet. Each real feature replaces
 * its placeholder in later phases.
 */
export function PagePlaceholder({
  title,
  phase,
  summary,
  children,
}: {
  title: string;
  phase: string;
  summary: string;
  children?: ReactNode;
}) {
  return (
    <section className="mx-auto max-w-3xl space-y-6">
      <div className="space-y-2">
        <span className="inline-block rounded-full bg-emerald-600/10 px-3 py-1 text-xs font-medium text-emerald-700 dark:text-emerald-300">
          {phase}
        </span>
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        <p className="text-neutral-600 dark:text-neutral-400">{summary}</p>
      </div>
      {children ? (
        <div className="rounded-lg border border-neutral-200 bg-white p-6 shadow-sm dark:border-neutral-800 dark:bg-neutral-900">
          {children}
        </div>
      ) : null}
    </section>
  );
}
