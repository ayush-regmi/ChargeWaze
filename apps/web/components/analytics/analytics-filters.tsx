import type { GroupBy } from "./format";

export type VehicleOption = { id: string; label: string };

const FIELD =
  "mt-1 block rounded-md border border-neutral-300 bg-white px-2 py-1.5 text-sm dark:border-neutral-700 dark:bg-neutral-900";
const LABEL = "text-xs font-medium text-neutral-600 dark:text-neutral-400";

/**
 * The Analytics filters as a plain GET form, so they work without JavaScript
 * and every view has a shareable URL. Submitting drops the table cursor.
 */
export function AnalyticsFilters({
  vehicles,
  vehicleId,
  from,
  to,
  groupBy,
  includeSimulated,
  tz,
}: {
  vehicles: readonly VehicleOption[];
  vehicleId: string;
  from: string;
  to: string;
  groupBy: GroupBy;
  includeSimulated: boolean;
  tz: string | null;
}) {
  return (
    <form
      method="get"
      action="/analytics"
      className="flex flex-wrap items-end gap-3"
      aria-label="Analytics filters"
    >
      {vehicles.length > 1 ? (
        <label className={LABEL}>
          Vehicle
          <select name="vehicle" defaultValue={vehicleId} className={FIELD}>
            {vehicles.map((v) => (
              <option key={v.id} value={v.id}>
                {v.label}
              </option>
            ))}
          </select>
        </label>
      ) : (
        <input type="hidden" name="vehicle" value={vehicleId} />
      )}
      <label className={LABEL}>
        From
        <input
          type="date"
          name="from"
          defaultValue={from}
          required
          className={FIELD}
        />
      </label>
      <label className={LABEL}>
        To
        <input
          type="date"
          name="to"
          defaultValue={to}
          required
          className={FIELD}
        />
      </label>
      <label className={LABEL}>
        Group by
        <select name="group_by" defaultValue={groupBy} className={FIELD}>
          <option value="day">Day</option>
          <option value="week">Week</option>
          <option value="month">Month</option>
        </select>
      </label>
      <label className="flex items-center gap-2 pb-2 text-sm">
        <input
          type="checkbox"
          name="simulated"
          value="1"
          defaultChecked={includeSimulated}
        />
        Include simulated data
      </label>
      {tz ? <input type="hidden" name="tz" value={tz} /> : null}
      <button
        type="submit"
        className="rounded-md bg-emerald-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-emerald-700"
      >
        Apply
      </button>
    </form>
  );
}
