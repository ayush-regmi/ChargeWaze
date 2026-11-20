/**
 * Shared SVG geometry and axes for the analytics charts (phase5_plan D5).
 * Charts draw in a fixed viewBox and scale to their container's width.
 */

import { formatTick, type Scale, type Ticks } from "./scale";

export const WIDTH = 480;
export const HEIGHT = 220;
export const MARGIN = { top: 12, right: 12, bottom: 40, left: 52 } as const;
export const PLOT = {
  left: MARGIN.left,
  right: WIDTH - MARGIN.right,
  top: MARGIN.top,
  bottom: HEIGHT - MARGIN.bottom,
} as const;

const AXIS_TEXT = "fill-muted-foreground text-[10px]";
const GRID = "stroke-border";

/** Horizontal grid lines with value labels on the left, and the y-axis title. */
export function YAxis({
  ticks,
  y,
  label,
}: {
  ticks: Ticks;
  y: Scale;
  label: string;
}) {
  return (
    <g aria-hidden="true">
      {ticks.ticks.map((t) => (
        <g key={t}>
          <line
            x1={PLOT.left}
            x2={PLOT.right}
            y1={y(t)}
            y2={y(t)}
            className={GRID}
          />
          <text
            x={PLOT.left - 6}
            y={y(t)}
            dy="0.32em"
            textAnchor="end"
            className={AXIS_TEXT}
          >
            {formatTick(t, ticks.step)}
          </text>
        </g>
      ))}
      <text
        transform={`translate(12 ${(PLOT.top + PLOT.bottom) / 2}) rotate(-90)`}
        textAnchor="middle"
        className={AXIS_TEXT}
      >
        {label}
      </text>
    </g>
  );
}

/** Numeric x-axis: tick marks and labels under the plot, and the axis title. */
export function XAxisNumeric({
  ticks,
  x,
  label,
}: {
  ticks: Ticks;
  x: Scale;
  label: string;
}) {
  return (
    <g aria-hidden="true">
      {ticks.ticks.map((t) => (
        <g key={t}>
          <line
            x1={x(t)}
            x2={x(t)}
            y1={PLOT.top}
            y2={PLOT.bottom}
            className={GRID}
          />
          <text
            x={x(t)}
            y={PLOT.bottom + 14}
            textAnchor="middle"
            className={AXIS_TEXT}
          >
            {formatTick(t, ticks.step)}
          </text>
        </g>
      ))}
      <text
        x={(PLOT.left + PLOT.right) / 2}
        y={HEIGHT - 6}
        textAnchor="middle"
        className={AXIS_TEXT}
      >
        {label}
      </text>
    </g>
  );
}

/** Category labels (bucket names) under the plot; thinned to about 5 so they don't overlap. */
export function XAxisCategories({
  labels,
  center,
}: {
  labels: readonly string[];
  center: (i: number) => number;
}) {
  const every = Math.max(1, Math.ceil(labels.length / 5));
  return (
    <g aria-hidden="true">
      <line
        x1={PLOT.left}
        x2={PLOT.right}
        y1={PLOT.bottom}
        y2={PLOT.bottom}
        className="stroke-border"
      />
      {labels.map((label, i) =>
        i % every === 0 ? (
          <text
            key={label}
            x={center(i)}
            y={PLOT.bottom + 16}
            textAnchor="middle"
            className={AXIS_TEXT}
          >
            {label}
          </text>
        ) : null,
      )}
    </g>
  );
}
