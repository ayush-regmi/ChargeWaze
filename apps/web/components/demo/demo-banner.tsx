import { FlaskConical } from "lucide-react";

import { env } from "@/lib/env";

/**
 * Persistent notice on every app page of the public demo (P6-F2,
 * architecture.md §7.2): the data is simulated, and the shared demo account
 * is read-only except for planning trips. Renders nothing on a live deployment.
 */
export function DemoBanner({ enabled = env.demoMode }: { enabled?: boolean }) {
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
