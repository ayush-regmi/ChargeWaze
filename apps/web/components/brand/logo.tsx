import { Zap } from "lucide-react";

import { cn } from "@/lib/utils";

/** Monochrome Chargewise mark: a bolt in a small rounded square. */
export function LogoMark({ className }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={cn(
        "inline-flex size-6 items-center justify-center rounded-md bg-primary text-primary-foreground",
        className,
      )}
    >
      <Zap className="size-3.5" strokeWidth={2.5} fill="currentColor" />
    </span>
  );
}

/** Mark plus wordmark. */
export function Logo({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2 text-sm font-semibold tracking-tight", className)}>
      <LogoMark />
      <span>Chargewise</span>
    </span>
  );
}
