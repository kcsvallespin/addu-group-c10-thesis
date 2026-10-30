import { NextResponse } from "next/server";
import { generateItinerary, InfeasibleError } from "@/lib/generate";
import type { GenerateRequest, Interest, ModelChoice, WeatherPattern } from "@/lib/types";

export const maxDuration = 300;

const VALID_INTERESTS: Interest[] = [
  "Food",
  "Nature",
  "Culture",
  "Entertainment",
  "Shopping",
  "Wellness",
  "Accommodation",
  "Sightseeing",
];
const VALID_WEATHER: WeatherPattern[] = ["clear", "mixed", "rain", "live"];
const VALID_MODELS: ModelChoice[] = ["main", "baseline"];

export async function POST(request: Request) {
  let body: Partial<GenerateRequest>;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const durationDays = Number(body.duration_days);
  const budgetPerPerson = Number(body.budget_per_person_php);
  const groupSize = Number(body.group_size);
  const interests = body.interests;
  const weatherPattern = body.weather_pattern;
  const startDate = body.start_date;
  const model = body.model;

  if (!Number.isInteger(durationDays) || durationDays < 1 || durationDays > 7) {
    return NextResponse.json({ error: "duration_days must be an integer between 1 and 7" }, { status: 400 });
  }
  if (!Number.isInteger(budgetPerPerson) || budgetPerPerson <= 0) {
    // whole pesos, like the benchmark scenarios (keeps the prompt identical to batch_runner.py's)
    return NextResponse.json({ error: "budget_per_person_php must be a positive whole number" }, { status: 400 });
  }
  if (!Number.isInteger(groupSize) || groupSize < 1 || groupSize > 20) {
    return NextResponse.json({ error: "group_size must be an integer between 1 and 20" }, { status: 400 });
  }
  if (
    !Array.isArray(interests) ||
    interests.length === 0 ||
    !interests.every((i) => VALID_INTERESTS.includes(i)) ||
    new Set(interests).size !== interests.length
  ) {
    return NextResponse.json(
      { error: `interests must be a non-empty array of distinct values from ${JSON.stringify(VALID_INTERESTS)}` },
      { status: 400 }
    );
  }
  if (!weatherPattern || !VALID_WEATHER.includes(weatherPattern)) {
    return NextResponse.json({ error: `weather_pattern must be one of ${JSON.stringify(VALID_WEATHER)}` }, { status: 400 });
  }
  if (
    typeof startDate !== "string" ||
    !/^\d{4}-\d{2}-\d{2}$/.test(startDate) ||
    Number.isNaN(Date.parse(`${startDate}T00:00:00Z`)) ||
    new Date(`${startDate}T00:00:00Z`).toISOString().slice(0, 10) !== startDate // rejects e.g. 2026-02-30
  ) {
    return NextResponse.json({ error: "start_date must be YYYY-MM-DD" }, { status: 400 });
  }
  if (!model || !VALID_MODELS.includes(model)) {
    return NextResponse.json({ error: `model must be one of ${JSON.stringify(VALID_MODELS)}` }, { status: 400 });
  }
  if (weatherPattern === "live" && durationDays > 5) {
    return NextResponse.json(
      { error: "Live weather only covers about 5 days ahead; use 5 days or fewer, or pick a fixed weather pattern." },
      { status: 400 }
    );
  }

  try {
    const result = await generateItinerary({
      duration_days: durationDays,
      budget_per_person_php: budgetPerPerson,
      group_size: groupSize,
      interests,
      weather_pattern: weatherPattern,
      start_date: startDate,
      model,
    });
    return NextResponse.json(result);
  } catch (e) {
    if (e instanceof InfeasibleError) {
      return NextResponse.json({ error: e.message }, { status: 422 });
    }
    const message = e instanceof Error ? e.message : "Unknown error";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
