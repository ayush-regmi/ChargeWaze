import { ChartDataTable } from "./data-table";
import { HEIGHT, PLOT, WIDTH, XAxisNumeric, YAxis } from "./frame";
import { linearScale, niceTicks } from "./scale";

export type ScatterPoint = {
  id: string;
  /** Row header in the data table (e.g. the trip's end time). */
  rowLabel: string;
  x: number;
  y: number;
  /** Drawn as an outline instead of a filled dot (low-quality trips). */
  hollow: boolean;
  /** Extra text for the data table's last column (e.g. the quality). */
  note: string;
};

/**
 * Per-trip estimated (x) vs observed (y) with a y = x reference line: points
 * above it used more energy than the model expected. Both axes share one
 * domain so the diagonal is a true 45° line.
 */
export function ScatterChart({
  label,
  xLabel,
  yLabel,
  columns,
  points,
  format,
}: {
  label: string;
  xLabel: string;
  yLabel: string;
  /** Data table headers: row, x, y, note. */
  columns: readonly [string, string, string, string];
  points: readonly ScatterPoint[];
  format: (value: number) => string;
}) {
  const max = Math.max(0, ...points.flatMap((p) => [p.x, p.y]));
  const ticks = niceTicks(0, max);
  const x = linearScale(ticks.domain, [PLOT.left, PLOT.right]);
  const y = linearScale(ticks.domain, [PLOT.bottom, PLOT.top]);
  const [d0, d1] = ticks.domain;

  return (
    <figure>
      <svg
        role="img"
        aria-label={label}
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="mx-auto h-auto w-full max-w-xl"
        data-chart="scatter"
      >
        <YAxis ticks={ticks} y={y} label={yLabel} />
        <XAxisNumeric ticks={ticks} x={x} label={xLabel} />
        <line
          x1={x(d0)}
          y1={y(d0)}
          x2={x(d1)}
          y2={y(d1)}
          strokeDasharray="4"
          className="stroke-muted-foreground"
          data-reference="y=x"
        >
          <title>Observed equals estimated</title>
        </line>
        {points.map((p) => (
          <circle
            key={p.id}
            cx={x(p.x)}
            cy={y(p.y)}
            r={4.5}
            strokeWidth={1.5}
            data-hollow={p.hollow ? "true" : "false"}
            className={
              p.hollow
                ? "fill-transparent stroke-warning"
                : "fill-chart-2 stroke-chart-2"
            }
          >
            <title>{`${p.rowLabel}: estimated ${format(p.x)}, observed ${format(p.y)} (${p.note})`}</title>
          </circle>
        ))}
      </svg>
      <ChartDataTable
        caption={label}
        columns={columns}
        rows={points.map((p) => [p.rowLabel, format(p.x), format(p.y), p.note])}
      />
    </figure>
  );
}
