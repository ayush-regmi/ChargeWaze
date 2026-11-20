"use client";

import { RefreshCw } from "lucide-react";
import { useState, useTransition } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { refreshVehicle } from "./refresh-actions";
import { TimeAgo } from "./time-ago";

type Message = { tone: "info" | "error"; content: React.ReactNode };

/**
 * "Refresh" for a vehicle whose data is stale (ADR 0010): one live read from
 * Smartcar to fill a gap when webhooks were missed. On success the server
 * action revalidates the page, so fresh data replaces the stale banner.
 */
export function RefreshButton({ vehicleId, className }: { vehicleId: string; className?: string }) {
  const [pending, startTransition] = useTransition();
  const [message, setMessage] = useState<Message | null>(null);

  function onClick() {
    setMessage(null);
    startTransition(async () => {
      const result = await refreshVehicle(vehicleId);
      if (!result.ok) {
        setMessage({ tone: "error", content: result.error });
      } else if (result.isStale) {
        setMessage({
          tone: "info",
          content: result.recordedAt ? (
            <>
              Your car hasn&apos;t reported anything newer. Its last report was{" "}
              <TimeAgo iso={result.recordedAt} />.
            </>
          ) : (
            "Your car hasn't reported any data yet."
          ),
        });
      }
    });
  }

  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={onClick}
        disabled={pending}
        className="self-start"
      >
        <RefreshCw aria-hidden className={cn(pending && "animate-spin")} />
        {pending ? "Refreshing…" : "Refresh"}
      </Button>
      {message ? (
        <p
          role={message.tone === "error" ? "alert" : "status"}
          data-testid="refresh-message"
          className={cn(
            "text-xs",
            message.tone === "error" ? "text-destructive" : "text-muted-foreground",
          )}
        >
          {message.content}
        </p>
      ) : null}
    </div>
  );
}
