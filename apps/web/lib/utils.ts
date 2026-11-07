/**
 * Tiny class-name joiner. Mirrors the behavior of `clsx` for the handful of
 * cases Chargewise needs without pulling the dep in just yet.
 */
export function cn(...classes: Array<string | false | null | undefined>): string {
  return classes.filter(Boolean).join(" ");
}
