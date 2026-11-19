import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * Page title row: title and an optional one-line description (inline content
 * only; it renders in a `<p>`) on the left,
 * actions on the right (stacked under the title on narrow screens).
 */
export function PageHeader({
  title,
  description,
  actions,
  id,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  id?: string;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between",
        className,
      )}
    >
      <div className="min-w-0 space-y-1">
        <h1 id={id} className="text-2xl font-semibold tracking-tight">
          {title}
        </h1>
        {description ? <p className="text-sm text-muted-foreground">{description}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}
