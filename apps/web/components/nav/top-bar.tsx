import Link from "next/link";

/**
 * App top bar: brand on the left, placeholder user affordance on the right.
 * Sign-out and user avatar are wired in Slice 2.
 */
export function TopBar() {
  return (
    <header className="flex h-14 items-center justify-between border-b border-neutral-200 bg-white px-4 dark:border-neutral-800 dark:bg-neutral-950">
      <Link href="/dashboard" className="flex items-center gap-2 font-semibold">
        <span
          aria-hidden
          className="inline-flex h-7 w-7 items-center justify-center rounded-md bg-emerald-600 text-white"
        >
          CW
        </span>
        <span>Chargewise</span>
      </Link>
      <span className="text-xs text-neutral-500 dark:text-neutral-400">
        specification phase
      </span>
    </header>
  );
}
