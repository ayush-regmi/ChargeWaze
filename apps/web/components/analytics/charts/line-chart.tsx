import { ChartDataTable } from "./data-table";
import { HEIGHT, PLOT, WIDTH, XAxisCategories, YAxis } from "./frame";
import { linearScale, niceTicks } from "./scale";

export type LineSeries = {
  name: string;
  /** One value per category; null leaves a gap (no data in that bucket). */
  values: readonly (number | null)[];
  variant: "primary" | "secondary";
};

const STYLES = {
  primary: {
    stroke: "stroke-emerald-600 dark:stroke-emerald-400",
    fill: "fill-emerald-600 dark:fill-emerald-400",
    dash: undefined,
  },
  secondary: {
    stroke: "stroke-blue-600 dark:stroke-blue-400",
    fill: "fill-blue-600 dark:fill-blue-400",
    dash: "6 4",
  },
} as const;

/** Runs of consecutive non-null points, so a missing bucket breaks the line. */
export function segments(
  values: readonly (number | null)[],
): { i: number; v: number }[][] {
  const out: { i: number; v: number }[][] = [];
  let run: { i: number; v: number }[] = [];
  values.forEach((v, i) => {
    if (v === null || !Number.isFinite(v)) {
      if (run.length) out.push(run);
      run = [];
    } else {
      run.push({ i, v });
    }
  });
  if (run.length) out.push(run);
  return out;
}

/** Several series over the same categories (e.g. observed vs estimated per week). */
export function LineChart({
  label,
  yLabel,
  categoryHeader,
  labels,
  series,
  format,
}: {
  label: string;
  yLabel: string;
  categoryHeader: string;
  labels: readonly string[];
  series: readonly LineSeries[];
  format: (value: number) => string;
}) {
  const all = series.flatMap((s) =>
    s.values.filter((v): v is number => v !== null && Number.isFinite(v)),
  );
  const ticks = niceTicks(0, Math.max(0, ...all));
  const y = linearScale(ticks.domain, [PLOT.bottom, PLOT.top]);
  const slot = (PLOT.right - PLOT.left) / Math.max(1, labels.length);
  const center = (i: number) => PLOT.left + slot * (i + 0.5);

  return (
    <figure>
      <ul
        className="mb-1 flex flex-wrap gap-4 text-xs text-neutral-600 dark:text-neutral-400"
        aria-hidden="true"
      >
        {series.map((s) => (
          <li key={s.name} className="flex items-center gap-1.5">
            <svg width="20" height="8">
              <line
                x1="0"
                x2="20"
                y1="4"
                y2="4"
                strokeWidth="2"
                strokeDasharray={STYLES[s.variant].dash}
                className={STYLES[s.variant].stroke}
              />
            </svg>
            {s.name}
          </li>
        ))}
      </ul>
      <svg
        role="img"
        aria-label={label}
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="h-auto w-full"
        data-chart="line"
      >
        <YAxis ticks={ticks} y={y} label={yLabel} />
        <XAxisCategories labels={labels} center={center} />
        {series.map((s) => (
          <g key={s.name} data-series={s.name}>
            {segments(s.values).map((run) => (
              <polyline
                key={run[0].i}
                points={run.map((p) => `${center(p.i)},${y(p.v)}`).join(" ")}
                fill="none"
                strokeWidth={2}
                strokeDasharray={STYLES[s.variant].dash}
                className={STYLES[s.variant].stroke}
              />
            ))}
            {s.values.map((v, i) =>
              v === null || !Number.isFinite(v) ? null : (
                <circle
                  key={i}
                  cx={center(i)}
                  cy={y(v)}
                  r={3.5}
                  className={STYLES[s.variant].fill}
                >
                  <title>{`${labels[i]}, ${s.name}: ${format(v)}`}</title>
                </circle>
              ),
            )}
          </g>
        ))}
      </svg>
      <ChartDataTable
        caption={label}
        columns={[categoryHeader, ...series.map((s) => s.name)]}
        rows={labels.map((l, i) => [
          l,
          ...series.map((s) => {
            const v = s.values[i];
            return v === null || v === undefined || !Number.isFinite(v)
              ? "—"
              : format(v);
          }),
        ])}
      />
    </figure>
  );
}
