import type { ReactNode } from "react";

import { DataAttribution } from "@/components/map/attribution";
import { LocalTime } from "@/components/garage/local-time";
import {
  accessLabel,
  addressLines,
  connectorLabel,
  directionsLinks,
  formatKw,
  statusLabel,
  type Station,
} from "@/lib/stations";

import { CompatibilityBadge, PowerUnknownBadge } from "./station-card";

const NOT_REPORTED = "Not reported";

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[9rem_1fr] gap-3 py-2 text-sm">
      <dt className="text-neutral-600 dark:text-neutral-400">{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

/**
 * Station detail (phase3_plan F6): connectors, operator/network, address,
 * access, status and freshness. Missing values read "Not reported"; nothing
 * is derived or guessed (PRD §7).
 */
export function StationDetail({ station }: { station: Station }) {
  const address = addressLines(station);
  const links = directionsLinks(station.location);
  return (
    <article aria-labelledby="station-title" className="space-y-6">
      <header className="space-y-2">
        <h1 id="station-title" className="text-2xl font-semibold tracking-tight">
          {station.name}
        </h1>
        <div className="flex flex-wrap gap-1.5">
          <CompatibilityBadge compatible={station.compatible} />
          {station.max_power_kw === null ? <PowerUnknownBadge /> : null}
        </div>
      </header>

      {station.is_stale ? (
        <p
          role="status"
          className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:bg-amber-950/50 dark:text-amber-300"
        >
          This information may be out of date: the station provider couldn&apos;t be reached, so
          this is the last copy Chargewise saved.
        </p>
      ) : null}

      <section aria-labelledby="connectors-title" className="space-y-2">
        <h2 id="connectors-title" className="text-sm font-medium">
          Connectors
        </h2>
        {station.connectors.length === 0 ? (
          <p className="text-sm text-neutral-500">Connector types not reported.</p>
        ) : (
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-neutral-500">
              <tr>
                <th scope="col" className="py-1 font-medium">
                  Type
                </th>
                <th scope="col" className="py-1 font-medium">
                  Count
                </th>
                <th scope="col" className="py-1 font-medium">
                  Power
                </th>
              </tr>
            </thead>
            <tbody>
              {station.connectors.map((c) => (
                <tr key={c.type} className="border-t border-neutral-200 dark:border-neutral-800">
                  <td className="py-1.5">{connectorLabel(c.type)}</td>
                  <td className="py-1.5">{c.count ?? "Unknown"}</td>
                  <td className="py-1.5">{formatKw(c.power_kw) ?? "Unknown"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <dl className="divide-y divide-neutral-200 dark:divide-neutral-800">
        <Row label="Network">{station.network ?? NOT_REPORTED}</Row>
        <Row label="Operator">{station.operator ?? NOT_REPORTED}</Row>
        <Row label="Address">
          {address.length > 0 ? (
            <address className="not-italic">
              {address.map((line) => (
                <span key={line} className="block">
                  {line}
                </span>
              ))}
            </address>
          ) : (
            NOT_REPORTED
          )}
        </Row>
        <Row label="Access">
          {accessLabel(station.access)}
          {station.restricted_access ? " (restricted)" : null}
          {station.access_detail ? (
            <span className="block text-xs text-neutral-500">{station.access_detail}</span>
          ) : null}
        </Row>
        <Row label="Status">{statusLabel(station.status)}</Row>
        <Row label="Pricing">{station.pricing_text ?? NOT_REPORTED}</Row>
        <Row label="Live availability">Not reported by the provider</Row>
        <Row label="Provider updated">
          {station.source_updated_at ? <LocalTime iso={station.source_updated_at} /> : NOT_REPORTED}
        </Row>
        <Row label="Fetched">
          <LocalTime iso={station.fetched_at} />
        </Row>
      </dl>

      <section aria-labelledby="directions-title" className="space-y-2">
        <h2 id="directions-title" className="text-sm font-medium">
          Directions
        </h2>
        <div className="flex flex-wrap gap-3 text-sm">
          <a
            href={links.google}
            target="_blank"
            rel="noopener noreferrer"
            className="font-medium text-blue-700 hover:underline dark:text-blue-400"
          >
            Open in Google Maps
          </a>
          <a
            href={links.apple}
            target="_blank"
            rel="noopener noreferrer"
            className="font-medium text-blue-700 hover:underline dark:text-blue-400"
          >
            Open in Apple Maps
          </a>
        </div>
      </section>

      <DataAttribution />
    </article>
  );
}
