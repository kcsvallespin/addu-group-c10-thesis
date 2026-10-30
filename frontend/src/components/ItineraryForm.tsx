"use client";

import { useEffect, useState } from "react";
import type { GenerateRequest, Interest, ModelChoice, WeatherPattern } from "@/lib/types";

const INTERESTS: Interest[] = [
  "Food",
  "Nature",
  "Culture",
  "Entertainment",
  "Shopping",
  "Wellness",
  "Accommodation",
  "Sightseeing",
];

const MODEL_LABELS: Record<ModelChoice, string> = {
  main: "Main model — MiMo V2.5 Pro",
  baseline: "Baseline — DeepSeek R1",
};

function tomorrow(): string {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; // local date, not UTC
}

export function ItineraryForm({
  onSubmit,
  disabled,
}: {
  onSubmit: (req: GenerateRequest) => void;
  disabled: boolean;
}) {
  const [durationDays, setDurationDays] = useState(2);
  const [budgetPerPerson, setBudgetPerPerson] = useState(1000);
  const [groupSize, setGroupSize] = useState(2);
  const [interests, setInterests] = useState<Interest[]>(["Food", "Nature"]);
  const [weatherPattern, setWeatherPattern] = useState<WeatherPattern>("clear");
  const [startDate, setStartDate] = useState("");
  const [model, setModel] = useState<ModelChoice>("main");

  // The current date isn't knowable at prerender time, so it's read client-side on mount instead.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setStartDate(tomorrow());
  }, []);

  function toggleInterest(i: Interest) {
    setInterests((prev) => (prev.includes(i) ? prev.filter((x) => x !== i) : [...prev, i]));
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (interests.length === 0 || !startDate) return;
    onSubmit({
      duration_days: durationDays,
      budget_per_person_php: budgetPerPerson,
      group_size: groupSize,
      interests,
      weather_pattern: weatherPattern,
      start_date: startDate,
      model,
    });
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6 rounded-2xl bg-white p-6 shadow-sm ring-1 ring-slate-200">
      <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
        <label className="block">
          <span className="text-sm font-medium text-slate-700">Trip duration (days)</span>
          <input
            type="number"
            min={1}
            max={7}
            value={durationDays}
            onChange={(e) => setDurationDays(Number(e.target.value))}
            className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-slate-900 focus:border-teal-500 focus:outline-none focus:ring-1 focus:ring-teal-500"
            required
          />
        </label>

        <label className="block">
          <span className="text-sm font-medium text-slate-700">Group size</span>
          <input
            type="number"
            min={1}
            max={20}
            value={groupSize}
            onChange={(e) => setGroupSize(Number(e.target.value))}
            className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-slate-900 focus:border-teal-500 focus:outline-none focus:ring-1 focus:ring-teal-500"
            required
          />
        </label>

        <label className="block">
          <span className="text-sm font-medium text-slate-700">Budget per person, per day (PHP)</span>
          <input
            type="number"
            min={1}
            step={1}
            value={budgetPerPerson}
            onChange={(e) => setBudgetPerPerson(Math.round(Number(e.target.value)))}
            className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-slate-900 focus:border-teal-500 focus:outline-none focus:ring-1 focus:ring-teal-500"
            required
          />
        </label>

        <label className="block">
          <span className="text-sm font-medium text-slate-700">Start date</span>
          <input
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
            disabled={weatherPattern === "live"}
            className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-slate-900 focus:border-teal-500 focus:outline-none focus:ring-1 focus:ring-teal-500 disabled:bg-slate-100 disabled:text-slate-400"
            required
          />
          {weatherPattern === "live" && (
            <p className="mt-1 text-xs text-slate-500">Ignored for live weather — the forecast always starts tomorrow.</p>
          )}
        </label>

        <label className="block">
          <span className="text-sm font-medium text-slate-700">Weather forecast</span>
          <select
            value={weatherPattern}
            onChange={(e) => setWeatherPattern(e.target.value as WeatherPattern)}
            className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-slate-900 focus:border-teal-500 focus:outline-none focus:ring-1 focus:ring-teal-500"
          >
            <option value="clear">Clear for the whole trip</option>
            <option value="mixed">Mixed (alternating rain)</option>
            <option value="rain">Rain for the whole trip</option>
            <option value="live">Live forecast (Davao City)</option>
          </select>
          {weatherPattern === "live" && durationDays > 5 && (
            <p className="mt-1 text-xs text-red-600">Live weather only covers about 5 days ahead — reduce the trip duration.</p>
          )}
        </label>

        <label className="block">
          <span className="text-sm font-medium text-slate-700">Model</span>
          <select
            value={model}
            onChange={(e) => setModel(e.target.value as ModelChoice)}
            className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-slate-900 focus:border-teal-500 focus:outline-none focus:ring-1 focus:ring-teal-500"
          >
            {(Object.keys(MODEL_LABELS) as ModelChoice[]).map((m) => (
              <option key={m} value={m}>
                {MODEL_LABELS[m]}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div>
        <span className="text-sm font-medium text-slate-700">Interests</span>
        <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
          {INTERESTS.map((i) => {
            const active = interests.includes(i);
            return (
              <button
                key={i}
                type="button"
                onClick={() => toggleInterest(i)}
                aria-pressed={active}
                className={`rounded-lg px-3 py-2 text-sm font-medium transition ${
                  active
                    ? "bg-teal-600 text-white"
                    : "bg-slate-100 text-slate-700 hover:bg-slate-200"
                }`}
              >
                {i}
              </button>
            );
          })}
        </div>
        {interests.length === 0 && <p className="mt-1 text-sm text-red-600">Pick at least one interest.</p>}
      </div>

      <button
        type="submit"
        disabled={disabled || interests.length === 0 || (weatherPattern === "live" && durationDays > 5)}
        className="w-full rounded-lg bg-teal-600 px-4 py-3 font-semibold text-white transition hover:bg-teal-700 disabled:cursor-not-allowed disabled:bg-slate-300"
      >
        {disabled ? "Generating..." : "Generate itinerary"}
      </button>
    </form>
  );
}
