import Link from "next/link";

/**
 * Simulated data is never mixed in silently (phase5_plan D11, contract §3.11):
 * a notice when simulated trips were left out, and a persistent banner when
 * they are included.
 */
export function SimulatedNotice({
  includesSimulated,
  excludedSimulatedTrips,
  includeHref,
  excludeHref,
}: {
  includesSimulated: boolean;
  excludedSimulatedTrips: number;
  /** The same view with simulated data included. */
  includeHref: string;
  /** The same view with simulated data left out. */
  excludeHref: string;
}) {
  if (includesSimulated) {
    return (
      <div
        role="status"
        data-testid="simulated-banner"
        className="rounded-md border border-warning/30 bg-warning/10 px-4 py-3 text-sm text-warning"
      >
        <span className="font-semibold">Includes simulated data.</span> Demo
        trips are mixed into these numbers and are marked “Simulated” in the
        table.{" "}
        <Link href={excludeHref} className="font-medium underline">
          Show real trips only
        </Link>
      </div>
    );
  }
  if (excludedSimulatedTrips > 0) {
    const trips =
      excludedSimulatedTrips === 1
        ? "1 simulated trip"
        : `${excludedSimulatedTrips} simulated trips`;
    return (
      <p
        role="status"
        data-testid="simulated-excluded"
        className="rounded-md border border-border px-4 py-3 text-sm text-foreground"
      >
        {trips} in this range {excludedSimulatedTrips === 1 ? "is" : "are"} left
        out.{" "}
        <Link
          href={includeHref}
          className="font-medium link"
        >
          Include simulated data
        </Link>
      </p>
    );
  }
  return null;
}
