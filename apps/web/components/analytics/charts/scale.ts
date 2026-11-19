/**
 * Linear scales and "nice" axis ticks for the analytics SVG charts
 * (phase5_plan D5: no chart library). Pure functions, no DOM.
 */

export type Scale = (value: number) => number;

/** Map `domain` linearly onto `range`. A zero-width domain maps to the range's midpoint. */
export function linearScale(
  domain: readonly [number, number],
  range: readonly [number, number],
): Scale {
  const [d0, d1] = domain;
  const [r0, r1] = range;
  if (d1 === d0) {
    const mid = (r0 + r1) / 2;
    return () => mid;
  }
  const k = (r1 - r0) / (d1 - d0);
  return (value) => r0 + (value - d0) * k;
}

/** The "nice" step (1, 2, 2.5 or 5 × 10^n) closest to `span / count`. */
export function niceStep(span: number, count: number): number {
  if (!(span > 0) || !(count > 0)) return 1;
  const raw = span / count;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const residual = raw / magnitude;
  const factor =
    residual <= 1
      ? 1
      : residual <= 2
        ? 2
        : residual <= 2.5
          ? 2.5
          : residual <= 5
            ? 5
            : 10;
  return factor * magnitude;
}

export type Ticks = {
  /** The axis domain, widened to whole steps so it starts and ends on a tick. */
  domain: [number, number];
  ticks: number[];
  step: number;
};

/**
 * About `count` evenly spaced round ticks covering [min, max]. An empty or
 * flat range is widened so the axis still has a sensible extent: [0, 0] → 0…1,
 * [5, 5] → 0…5 plus one step.
 */
export function niceTicks(min: number, max: number, count = 5): Ticks {
  let lo = Number.isFinite(min) ? min : 0;
  let hi = Number.isFinite(max) ? max : 0;
  if (lo > hi) [lo, hi] = [hi, lo];
  if (lo === hi) {
    if (lo === 0) {
      hi = 1;
    } else if (lo > 0) {
      lo = 0;
    } else {
      hi = 0;
    }
  }
  const step = niceStep(hi - lo, count);
  const start = Math.floor(lo / step) * step;
  const end = Math.ceil(hi / step) * step;
  const ticks: number[] = [];
  // Round away float noise (0.1 + 0.2) so labels print cleanly.
  const decimals = Math.max(0, -Math.floor(Math.log10(step)) + 1);
  for (let i = 0; start + i * step <= end + step / 2; i++) {
    ticks.push(Number((start + i * step).toFixed(decimals)));
  }
  return { domain: [ticks[0], ticks[ticks.length - 1]], ticks, step };
}

/** A tick label: no more decimals than the step needs. */
export function formatTick(value: number, step: number): string {
  const decimals = Math.min(
    3,
    (String(Number(step.toPrecision(6))).split(".")[1] ?? "").length,
  );
  return value.toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}
