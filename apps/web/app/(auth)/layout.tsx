import type { ReactNode } from "react";

/**
 * Centered, chrome-free layout for sign-in / sign-up / callback screens.
 */
export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-full flex-col items-center justify-center bg-neutral-50 p-4 dark:bg-neutral-950">
      <div className="w-full max-w-sm">{children}</div>
    </div>
  );
}
