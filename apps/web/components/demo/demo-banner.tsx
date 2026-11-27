import { FlaskConical } from "lucide-react";

import { sessionIsDemo } from "@/lib/session-api";

/**
 * Persistent notice on every app page while the public demo account is
 * signed in (P6-F2, ADR 0012, architecture.md §7.2): the data is simulated,
 * and the shared account is read-only except for planning trips.
 */
export async function DemoAccountBanner() {
  return <DemoBanner enabled={await sessionIsDemo()} />;
}

/** The banner itself; renders nothing unless `enabled`. */
export function DemoBanner({ enabled }: { enabled: boolean }) {
  if (!enabled) return null;
  return (
    <div
      role="status"
      data-testid="demo-banner"
      className="flex items-start gap-2 border-b border-info/30 bg-info/10 px-4 py-2 text-sm text-info md:px-10"
    >
      <FlaskConical className="mt-0.5 size-4 shrink-0" aria-hidden />
      <p>
        <span className="font-semibold">Public demo.</span> Everything here is
        simulated: a 2026 Toyota bZ with made-up drives and charging. You can
        plan new trips; everything else is read-only.
      </p>
    </div>
  );
}
