"use client";

import { useId, useState } from "react";

/**
 * The data behind a chart, as a table. It is always in the DOM (screen readers
 * and keyboard users read it); sighted users can show or hide it.
 */
export function ChartDataTable({
  caption,
  columns,
  rows,
}: {
  caption: string;
  columns: readonly string[];
  rows: readonly (readonly string[])[];
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <div className="mt-2">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((v) => !v)}
        className="text-xs font-medium text-blue-700 hover:underline dark:text-blue-400"
      >
        {open ? "Hide data" : "Show data"}
      </button>
      <div id={id} className={open ? "mt-2 overflow-x-auto" : "sr-only"}>
        <table className="w-full text-left text-xs">
          <caption className="sr-only">{caption}</caption>
          <thead>
            <tr className="border-b border-neutral-200 dark:border-neutral-800">
              {columns.map((c) => (
                <th key={c} scope="col" className="py-1 pr-3 font-medium">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr
                key={i}
                className="border-b border-neutral-100 dark:border-neutral-900"
              >
                {row.map((cell, j) =>
                  j === 0 ? (
                    <th key={j} scope="row" className="py-1 pr-3 font-normal">
                      {cell}
                    </th>
                  ) : (
                    <td key={j} className="py-1 pr-3 tabular-nums">
                      {cell}
                    </td>
                  ),
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
