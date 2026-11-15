import type { VehicleDetail } from "@/lib/vehicles";

/** Read-only specs (api §3.4). Editing comes with F7. */
export function SpecsPanel({ specs }: { specs: VehicleDetail["specs"] }) {
  const capacity = specs?.usable_capacity_kwh;
  const connectors = specs?.connectors ?? [];
  const curve = specs?.charging_curve ?? [];
  return (
    <div className="space-y-3 rounded-lg border border-neutral-200 bg-white p-4 shadow-sm dark:border-neutral-800 dark:bg-neutral-900">
      <h2 className="text-sm font-medium">Specs</h2>
      <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <dt className="text-xs uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
            Usable battery capacity
          </dt>
          <dd className="mt-1 text-sm">{capacity != null ? `${capacity} kWh` : "Not set"}</dd>
        </div>
        <div>
          <dt className="text-xs uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
            Connectors
          </dt>
          <dd className="mt-1 text-sm">
            {connectors.length > 0 ? connectors.join(", ") : "Not set"}
          </dd>
        </div>
      </dl>
      <div>
        <h3 className="text-xs uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
          Charging curve
        </h3>
        {curve.length > 0 ? (
          <table className="mt-2 text-sm">
            <thead>
              <tr className="text-left text-neutral-500 dark:text-neutral-400">
                <th className="pr-6 font-normal">Battery</th>
                <th className="font-normal">Max power</th>
              </tr>
            </thead>
            <tbody>
              {curve.map((p) => (
                <tr key={p.soc}>
                  <td className="pr-6">{Math.round(p.soc * 100)}%</td>
                  <td>{p.power_kw} kW</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="mt-1 text-sm">Not set</p>
        )}
      </div>
      {capacity == null ? (
        <p className="text-sm text-neutral-600 dark:text-neutral-400">
          Smartcar doesn&apos;t report battery capacity for this vehicle, and editing
          specs isn&apos;t available yet.
        </p>
      ) : null}
    </div>
  );
}
