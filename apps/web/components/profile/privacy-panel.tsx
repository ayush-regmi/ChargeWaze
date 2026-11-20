"use client";

import { useState, useTransition } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

import { formatRetention, type ConsentStatus, type PrivacySettings } from "./privacy";
import { recordPrivacyConsent } from "./privacy-actions";

/**
 * Profile "Privacy" (PRD §4.6): what Chargewise stores, how long, and the
 * user's answer to the privacy notice (`GET /api/v1/me/privacy`). Each answer
 * is stored as a new consent record, so the history stays auditable.
 */
export function PrivacyPanel({ privacy }: { privacy: PrivacySettings }) {
  const notice = privacy.consents.find((c) => c.kind === "privacy_notice");
  const { retention } = privacy;

  return (
    <section
      aria-label="Privacy"
      className="space-y-4 rounded-lg border border-border bg-background p-6"
    >
      <h2 className="text-sm font-medium">Privacy</h2>
      <div className="space-y-2 text-sm text-muted-foreground">
        <p>
          Chargewise stores what your connected cars report through Smartcar (battery level,
          range, odometer, charging status, and location when the car sends it), the trips you
          plan, the drives inferred from those readings, and your favorite stations. Nothing is
          shared with other users.
        </p>
        <ul className="list-disc space-y-1 pl-5">
          <li>Battery and vehicle readings are kept for {formatRetention(retention.telemetry_days)}.</li>
          <li>
            Plans you don&apos;t save are deleted after{" "}
            {formatRetention(retention.unsaved_plan_days)}.
          </li>
          <li>Deleting your account removes everything right away.</li>
        </ul>
      </div>
      {notice ? <NoticeConsent key={notice.current_version} notice={notice} /> : null}
    </section>
  );
}

function NoticeConsent({ notice }: { notice: ConsentStatus }) {
  const [current, setCurrent] = useState(notice);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  function answer(granted: boolean) {
    setError(null);
    startTransition(async () => {
      const result = await recordPrivacyConsent(current.current_version, granted);
      if ("error" in result) {
        setError(result.error);
        return;
      }
      setCurrent({
        ...current,
        granted: result.granted,
        version: current.current_version,
        recorded_at: new Date().toISOString(),
        is_current: result.granted,
      });
    });
  }

  const recordedAt = current.recorded_at ? new Date(current.recorded_at).toLocaleDateString() : null;

  return (
    <div className="space-y-3 border-t border-border pt-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">Privacy notice</span>
        {current.is_current ? (
          <Badge tone="success">Accepted</Badge>
        ) : current.granted === false ? (
          <Badge tone="warning">Declined</Badge>
        ) : (
          <Badge tone="warning">Not accepted</Badge>
        )}
      </div>
      <p className="text-sm text-muted-foreground">
        {current.is_current && recordedAt
          ? `You accepted version ${current.current_version} on ${recordedAt}.`
          : current.granted === true && current.version !== current.current_version
            ? `The notice changed since you accepted version ${current.version}. Review the summary above and accept version ${current.current_version}.`
            : current.granted === false && recordedAt
              ? `You declined version ${current.version} on ${recordedAt}.`
              : `Review the summary above and accept version ${current.current_version}.`}
      </p>
      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
      <div className="flex gap-2">
        {current.is_current ? (
          <Button variant="outline" size="sm" disabled={pending} onClick={() => answer(false)}>
            {pending ? "Saving…" : "Withdraw"}
          </Button>
        ) : (
          <Button variant="outline" size="sm" disabled={pending} onClick={() => answer(true)}>
            {pending ? "Saving…" : "Accept"}
          </Button>
        )}
      </div>
    </div>
  );
}
