import { PagePlaceholder } from "@/components/placeholder";

export default function DashboardPage() {
  return (
    <PagePlaceholder
      title="Dashboard"
      phase="Phase 2 placeholder"
      summary="Vehicle overview: current SoC, estimated range, charging status, odometer, and telemetry freshness."
    >
      <p className="text-sm text-neutral-600 dark:text-neutral-400">
        Wired in Phase 2 against{" "}
        <code className="rounded bg-neutral-100 px-1 py-0.5 text-xs dark:bg-neutral-800">
          GET /api/v1/vehicles/:id/state
        </code>
        . See{" "}
        <code className="rounded bg-neutral-100 px-1 py-0.5 text-xs dark:bg-neutral-800">
          docs/product-requirements.md
        </code>{" "}
        section 4.1.
      </p>
    </PagePlaceholder>
  );
}
