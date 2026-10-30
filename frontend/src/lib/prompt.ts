import { pyDumps } from "./pycompat";
import type { Candidate, Scenario, WeatherDay } from "./types";

// Port of run_scenario.py's SYSTEM_PROMPT / build_prompt() / repair_message(): the strings must stay identical.

const SYSTEM_PROMPT = `You are the itinerary planning engine for a Davao City tourism
application. Generate a structured, multi-day travel itinerary using
ONLY the candidate venues provided in the context payload below. Do
not invent, assume, or reference any venue not present in the
candidate list.

CONSTRAINTS:
- Trip duration: {duration_days} day(s)
- Daily budget ceiling: PHP {budget_php}
- Interest categories: {interests}
- Group size: {group_size}

RULES:
1. Each day must contain 3-5 scheduled venue visits with realistic
   time slots (e.g. 08:00-10:00).
2. NEVER use the same venue_id more than once in the whole itinerary.
3. Do not schedule a venue outside its operating_hours for that
   weekday. If operating_hours is null, hours are unknown: avoid the
   venue unless nothing else fits.
4. Do not schedule a Nature venue during a day where the weather
   context marks is_outdoor_suitable as false.
5. Each venue has est_group_cost_php: the estimated cost of ONE visit
   for the whole group. The sum of est_group_cost_php for all venues
   scheduled on a day MUST NOT exceed the daily budget ceiling. Use
   it; do not invent other peso amounts.
6. Cover every requested interest category across the trip, and
   sequence venues in a geographically reasonable order (latitude/
   longitude are given; a downstream validator optimizes the route).
7. Within a day, list slots in chronological order with NO overlap.
   Each visit lasts between 30 minutes and 4 hours and must fall
   between 06:00 and 23:00.
8. Return exactly one itinerary entry per trip day, using exactly the
   dates in the weather forecast.

Respond with ONLY a single JSON object with this shape:
{"itinerary": [{"day": int, "date": "YYYY-MM-DD", "slots": [{"time_start": "HH:MM",
"time_end": "HH:MM", "venue_id": "<id from candidates>"}]}]}
Do not include any explanation, markdown formatting, or text outside the JSON object.

CONTEXT PAYLOAD:
{context_payload_json}
`;

const REPAIR_PROMPT =
  "Your itinerary violated these constraints:\n- {problems}" +
  "\nFix every problem and return the full corrected JSON object only. " +
  "Keep using only venue_ids from the candidate list.";

export const INVALID_JSON_PROBLEM = "Response was not valid JSON. Reply with ONLY the JSON object.";

/** str.replace(old, new): every occurrence, and no "$&"-style patterns in the replacement. */
function replaceAll(s: string, find: string, value: string): string {
  return s.split(find).join(value);
}

export function buildPrompt(scenario: Scenario, candidates: Candidate[], weather: WeatherDay[]): string {
  const context = {
    user_constraints: {
      duration_days: scenario.duration_days,
      budget_php: scenario.budget_php,
      interests: scenario.interests,
      group_size: scenario.group_size,
    },
    weather_summary: { forecast_by_date: weather },
    candidate_venues: candidates,
  };
  let prompt = SYSTEM_PROMPT;
  prompt = replaceAll(prompt, "{duration_days}", String(scenario.duration_days));
  prompt = replaceAll(prompt, "{budget_php}", String(scenario.budget_php));
  prompt = replaceAll(prompt, "{interests}", scenario.interests.join(", "));
  prompt = replaceAll(prompt, "{group_size}", String(scenario.group_size));
  prompt = replaceAll(prompt, "{context_payload_json}", pyDumps(context));
  return prompt;
}

export function repairMessage(problems: string[]): string {
  return replaceAll(REPAIR_PROMPT, "{problems}", problems.join("\n- "));
}
