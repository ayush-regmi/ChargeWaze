"use client";

import { useId, useState } from "react";

import { LocalTime } from "@/components/garage/local-time";
import { formatSoc } from "@/components/trips/plan-payload";
import { Button } from "@/components/ui/button";
import { parseSocPercent, socPromptDefault } from "@/lib/plan-format";
import { fieldClass } from "@/components/ui/field";

/**
 * Asked when the car's battery reading is missing or older than the stale
 * threshold (`STARTING_SOC_REQUIRED`, api §3.7, phase4_plan D7). The answer is
 * sent as `starting_soc_override`; it's never guessed for the user.
 */
export function PlanSocPrompt({
  lastSoc,
  lastRecordedAt,
  pending,
  onSubmit,
}: {
  lastSoc: number | null;
  lastRecordedAt: string | null;
  pending: boolean;
  onSubmit: (soc: number) => void;
}) {
  const id = useId();
  const [value, setValue] = useState(socPromptDefault(lastSoc));
  const [error, setError] = useState<string | null>(null);

  return (
    <form
      aria-label="Starting battery"
      data-testid="plan-soc-prompt"
      className="space-y-2 rounded-lg border border-warning/30 bg-warning/10 p-4 text-sm text-warning"
      onSubmit={(e) => {
        e.preventDefault();
        const parsed = parseSocPercent(value);
        if ("error" in parsed) {
          setError(parsed.error);
          return;
        }
        setError(null);
        onSubmit(parsed.value);
      }}
    >
      <p className="font-semibold">How much charge will you start with?</p>
      <p>
        {lastSoc === null ? (
          "We don't have a battery reading from your car."
        ) : (
          <>
            Your car&apos;s last battery reading ({formatSoc(lastSoc)}
            {lastRecordedAt ? (
              <>
                , <LocalTime iso={lastRecordedAt} />
              </>
            ) : null}
            ) is too old to plan with. Confirm or correct it.
          </>
        )}
      </p>
      <div className="flex flex-wrap items-end gap-2">
        <div className="space-y-1">
          <label htmlFor={id} className="block text-xs font-medium">
            Starting battery (%)
          </label>
          <input
            id={id}
            inputMode="numeric"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? `${id}-error` : undefined}
            className={`${fieldClass} w-24`}
          />
        </div>
        <Button type="submit" size="sm" disabled={pending}>
          Plan with this charge
        </Button>
      </div>
      {error ? (
        <p id={`${id}-error`} role="alert" className="text-destructive">
          {error}
        </p>
      ) : null}
    </form>
  );
}
