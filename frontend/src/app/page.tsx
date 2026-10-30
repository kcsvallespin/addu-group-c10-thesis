"use client";

import { useState } from "react";
import { ItineraryForm } from "@/components/ItineraryForm";
import { ItineraryResults } from "@/components/ItineraryResults";
import type { GenerateError, GenerateRequest, GenerateResponse } from "@/lib/types";

type Status = "idle" | "loading" | "error" | "done";

export default function Home() {
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<GenerateResponse | null>(null);

  async function handleSubmit(req: GenerateRequest) {
    setStatus("loading");
    setError(null);
    try {
      const resp = await fetch("/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(req),
      });
      const data = (await resp.json()) as GenerateResponse | GenerateError;
      if (!resp.ok || "error" in data) {
        setError("error" in data ? data.error : "Something went wrong.");
        setStatus("error");
        return;
      }
      setResult(data);
      setStatus("done");
    } catch {
      setError("Could not reach the server. Is the dev server running?");
      setStatus("error");
    }
  }

  return (
    <div className="min-h-screen bg-slate-50">
      <main className="mx-auto max-w-3xl px-4 py-10 sm:px-6">
        <header className="mb-8 text-center">
          <h1 className="text-3xl font-bold tracking-tight text-slate-900">Davao Itinerary Generator</h1>
          <p className="mt-2 text-slate-600">
            Describe your trip and an LLM will build a day-by-day itinerary from real Davao City venues.
          </p>
        </header>

        <ItineraryForm onSubmit={handleSubmit} disabled={status === "loading"} />

        {status === "loading" && (
          <div className="mt-8 flex items-center justify-center gap-3 rounded-2xl bg-white p-6 text-slate-600 shadow-sm ring-1 ring-slate-200">
            <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-teal-600" />
            Generating your itinerary... this can take up to a few minutes, especially for the baseline model.
          </div>
        )}

        {status === "error" && error && (
          <div className="mt-8 rounded-2xl bg-red-50 p-5 text-sm text-red-800 shadow-sm ring-1 ring-red-200">
            <p className="font-medium">Couldn&apos;t generate an itinerary</p>
            <p className="mt-1">{error}</p>
          </div>
        )}

        {status === "done" && result && (
          <div className="mt-8">
            <ItineraryResults result={result} />
          </div>
        )}
      </main>
    </div>
  );
}
