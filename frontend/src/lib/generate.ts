import meta from "@/data/venues.meta.json";
import { checkOutput, shapeProblem } from "./checker";
import { callModel, getContent, parseItineraryJson, type ChatMessage } from "./openrouter";
import { buildPrompt, INVALID_JSON_PROBLEM, repairMessage } from "./prompt";
import { feasibility, loadCandidates } from "./venues";
import { getLiveWeather, weatherFor } from "./weather";
import type { AttemptLog, GenerateRequest, GenerateResponse, Itinerary, Scenario } from "./types";

// Port of batch_runner.py's run_one(): same candidates, prompt, checker and repair loop.

const TEMPERATURE = 0.2; // fixed for reproducibility, matches run_scenario.py
const MAX_ATTEMPTS = 3; // initial try + 2 repair rounds

export const MODEL_SLUGS: Record<GenerateRequest["model"], string> = {
  main: "xiaomi/mimo-v2.5-pro",
  baseline: "deepseek/deepseek-r1-0528",
};

/** The request cannot be satisfied by any itinerary from the candidate pool; no model call was made. */
export class InfeasibleError extends Error {
  constructor(public reasons: string[]) {
    super(`This trip can't be planned from the available venues: ${reasons.join("; ")}.`);
  }
}

export async function generateItinerary(req: GenerateRequest): Promise<GenerateResponse> {
  const scenario: Scenario = {
    duration_days: req.duration_days,
    budget_php: req.budget_per_person_php * req.group_size,
    interests: req.interests,
    group_size: req.group_size,
  };
  const model = MODEL_SLUGS[req.model];

  const candidates = loadCandidates(scenario);
  const weather =
    req.weather_pattern === "live"
      ? await getLiveWeather(req.duration_days)
      : weatherFor(req.weather_pattern, req.duration_days, req.start_date);
  const reasons = feasibility(candidates, scenario, weather);
  if (reasons.length) throw new InfeasibleError(reasons);

  const messages: ChatMessage[] = [{ role: "user", content: buildPrompt(scenario, candidates, weather) }];
  const attempts: AttemptLog[] = [];
  let itinerary: Itinerary | null = null;
  let promptTokens = 0;
  let completionTokens = 0;
  const t0 = Date.now();

  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    const data = await callModel(model, messages, TEMPERATURE);
    promptTokens += data.usage?.prompt_tokens ?? 0;
    completionTokens += data.usage?.completion_tokens ?? 0;
    const { text: raw, finishReason } = getContent(data);

    let problems: string[];
    let warnings: string[] = [];
    let parsed: unknown;
    try {
      parsed = parseItineraryJson(raw);
    } catch {
      parsed = undefined;
    }
    if (parsed === undefined) {
      itinerary = null;
      problems = [INVALID_JSON_PROBLEM];
    } else {
      const result = checkOutput(parsed, candidates, scenario, weather);
      problems = result.problems;
      warnings = result.warnings;
      itinerary = shapeProblem(parsed) ? null : (parsed as Itinerary); // only show replies the checker could walk
    }

    attempts.push({ attempt, problems, warnings, passed: problems.length === 0, finish_reason: finishReason });
    if (problems.length === 0) break;

    if (raw) messages.push({ role: "assistant", content: raw }); // skip the assistant turn when the reply was empty
    messages.push({ role: "user", content: repairMessage(problems) });
  }

  return {
    itinerary,
    data_version: meta.data_version,
    candidates,
    weather,
    scenario,
    attempts,
    final_passed: attempts[attempts.length - 1]?.passed ?? false,
    model_used: model,
    prompt_tokens: promptTokens,
    completion_tokens: completionTokens,
    seconds: Math.round((Date.now() - t0) / 1000),
  };
}
