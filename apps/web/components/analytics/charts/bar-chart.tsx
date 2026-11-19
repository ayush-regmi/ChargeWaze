import { ChartDataTable } from "./data-table";
import { HEIGHT, PLOT, WIDTH, XAxisCategories, YAxis } from "./frame";
import { linearScale, niceTicks } from "./scale";

export type Bar = { label: string; value: number };

/** One bar per bucket (e.g. distance per week). Plain SVG, phase5_plan D5. */
export function BarChart({
  label,
  yLabel,
  categoryHeader,
  valueHeader,
  bars,
  format,
}: {
  /** Accessible name of the chart and caption of its data table. */
  label: string;
  yLabel: string;
  categoryHeader: string;
  valueHeader: string;
  bars: readonly Bar[];
  format: (value: number) => string;
}) {
  const ticks = niceTicks(0, Math.max(0, ...bars.map((b) => b.value)));
  const y = linearScale(ticks.domain, [PLOT.bottom, PLOT.top]);
  const slot = (PLOT.right - PLOT.left) / Math.max(1, bars.length);
  const barWidth = Math.max(1, Math.min(48, slot * 0.7));
  const center = (i: number) => PLOT.left + slot * (i + 0.5);

  return (
    <figure>
      <svg
        role="img"
        aria-label={label}
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="h-auto w-full"
        data-chart="bar"
      >
        <YAxis ticks={ticks} y={y} label={yLabel} />
        <XAxisCategories labels={bars.map((b) => b.label)} center={center} />
        {bars.map((b, i) => {
          const top = y(b.value);
          return (
            <rect
              key={b.label}
              x={center(i) - barWidth / 2}
              y={top}
              width={barWidth}
              height={Math.max(0, PLOT.bottom - top)}
              rx={2}
              className="fill-emerald-600 dark:fill-emerald-500"
            >
              <title>{`${b.label}: ${format(b.value)}`}</title>
            </rect>
          );
        })}
      </svg>
      <ChartDataTable
        caption={label}
        columns={[categoryHeader, valueHeader]}
        rows={bars.map((b) => [b.label, format(b.value)])}
      />
    </figure>
  );
}
