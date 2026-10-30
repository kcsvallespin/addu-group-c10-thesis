"use client";

import { useState } from "react";
import type { GenerateResponse } from "@/lib/types";

function formatCost(php: number): string {
  return `PHP ${Math.round(php).toLocaleString()}`;
}

export function ItineraryResults({ result }: { result: GenerateResponse }) {
  const [showWarnings, setShowWarnings] = useState(false);
  const venueById = new Map(result.candidates.map((c) => [c.venue_id, c]));
  const weatherByDate = new Map(result.weather.map((w) => [w.date, w]));
  const lastAttempt = result.attempts[result.attempts.length - 1];
  const totalWarnings = result.attempts.flatMap((a) => a.warnings).length;

  return (
    <div className="space-y-6">
      <div className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-200">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <span
              className={`inline-flex items-center rounded-full px-3 py-1 text-sm font-semibold ${
                result.final_passed ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-800"
              }`}
            >
              {result.final_passed ? "Passed all checks" : "Failed some checks"}
            </span>
            <p className="mt-1 text-sm text-slate-500">
              {result.attempts.length} attempt{result.attempts.length > 1 ? "s" : ""} · {result.model_used} ·{" "}
              {result.seconds}s · {result.prompt_tokens.toLocaleString()} prompt / {result.completion_tokens.toLocaleString()} completion tokens
            </p>
          </div>
          {totalWarnings > 0 && (
            <button
              type="button"
              onClick={() => setShowWarnings((v) => !v)}
              className="text-sm font-medium text-teal-700 underline-offset-2 hover:underline"
            >
              {showWarnings ? "Hide" : "Show"} {totalWarnings} warning{totalWarnings > 1 ? "s" : ""}
            </button>
          )}
        </div>
        {lastAttempt && lastAttempt.problems.length > 0 && (
          <div className="mt-3 rounded-lg bg-red-50 p-3 text-sm text-red-800">
            <p className="font-medium">Unresolved after {result.attempts.length} attempts:</p>
            <ul className="mt-1 list-inside list-disc space-y-0.5">
              {lastAttempt.problems.map((p, i) => (
                <li key={i}>{p}</li>
              ))}
            </ul>
          </div>
        )}
        {showWarnings && (
          <div className="mt-3 rounded-lg bg-slate-50 p-3 text-sm text-slate-600">
            <ul className="list-inside list-disc space-y-0.5">
              {result.attempts.flatMap((a) => a.warnings).map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {!result.itinerary && (
        <div className="rounded-2xl bg-white p-5 text-sm text-slate-600 shadow-sm ring-1 ring-slate-200">
          The model&apos;s last reply was not a readable itinerary, so there is nothing to show.
        </div>
      )}

      {(result.itinerary?.itinerary ?? []).map((day) => {
        const weather = weatherByDate.get(day.date);
        const dayCost = (day.slots ?? []).reduce((sum, s) => sum + (venueById.get(s.venue_id)?.est_group_cost_php ?? 0), 0);
        return (
          <div key={day.day} className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-200">
            <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-slate-100 pb-3">
              <h3 className="text-lg font-semibold text-slate-900">
                Day {day.day} <span className="text-slate-400">·</span> {day.date}
              </h3>
              <div className="flex items-center gap-3 text-sm text-slate-500">
                {weather && (
                  <span className={weather.is_outdoor_suitable ? "text-emerald-600" : "text-blue-600"}>
                    {weather.condition}
                  </span>
                )}
                <span>{formatCost(dayCost)} est.</span>
              </div>
            </div>
            <ol className="mt-4 space-y-3">
              {(day.slots ?? []).map((slot, i) => {
                const v = venueById.get(slot.venue_id);
                return (
                  <li key={i} className="flex gap-4">
                    <div className="w-24 shrink-0 pt-0.5 text-sm font-medium text-slate-500">
                      {slot.time_start}–{slot.time_end}
                    </div>
                    <div className="flex-1 border-l-2 border-teal-200 pl-4">
                      <p className="font-medium text-slate-900">{v?.name ?? slot.venue_id}</p>
                      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-slate-500">
                        {v?.category.map((c) => (
                          <span key={c} className="rounded-full bg-slate-100 px-2 py-0.5">
                            {c}
                          </span>
                        ))}
                        {v && <span>{formatCost(v.est_group_cost_php)} for the group</span>}
                        {v?.average_rating != null && <span>★ {v.average_rating.toFixed(1)}</span>}
                      </div>
                    </div>
                  </li>
                );
              })}
            </ol>
          </div>
        );
      })}
    </div>
  );
}
