import venuesData from "@/data/venues.json";
import { PyRandom, fmtThousands, pyRound } from "./pycompat";
import type { Candidate, Scenario, Venue, WeatherDay } from "./types";

// Port of run_scenario.py's load_candidates() / feasibility(). The venue file is a copy of
// data-pipeline/benchmark_venues.json written by make_price_overrides.py - do not edit it by hand.

const PER_INTEREST = 8; // candidates pulled per requested interest (raised for long, narrow trips; see poolSizes)
const MAX_CANDIDATES = 24;
const SEED = 42;
const COST_SCALE = 1.0;

const VENUES = venuesData as Venue[];

/** Estimated peso cost of one visit for the whole group, from the hand-verified price. */
export function venueCost(v: Pick<Venue, "price_unit" | "price_php">, groupSize: number): number {
  if (v.price_unit === "room_night") return COST_SCALE * v.price_php * Math.ceil(groupSize / 2);
  return COST_SCALE * v.price_php * groupSize;
}

/** [perInterest, maxCandidates]: a trip needs at least 3 unique venues per day, so long trips with few
 * interests get a bigger pool (5 per day) than the default 8 per interest / 24 total. */
function poolSizes(scenario: Scenario): [number, number] {
  const need = 5 * scenario.duration_days;
  return [Math.max(PER_INTEREST, Math.ceil(need / scenario.interests.length)), Math.max(MAX_CANDIDATES, need)];
}

export function loadCandidates(scenario: Scenario): Candidate[] {
  const [perInterest, maxCandidates] = poolSizes(scenario);
  const rng = new PyRandom(SEED);
  const chosen: Venue[] = [];
  const seenIds = new Set<string>();
  const seenNames = new Set<string>();

  for (const interest of scenario.interests) {
    const pool = VENUES.filter(
      (v) => v.category.includes(interest) && v.latitude !== null && v.longitude !== null && v.operating_hours
    );
    // quality score: rating weighted by popularity (precomputed by make_price_overrides.py); sort is stable
    pool.sort((a, b) => b.quality_score - a.quality_score);
    const top = pool.slice(0, perInterest * 4); // shortlist, then sample for variety
    rng.shuffle(top);
    const cheap = [...top].sort((a, b) => venueCost(a, 1) - venueCost(b, 1)).slice(0, Math.floor(perInterest / 2));
    const cheapIds = new Set(cheap.map((v) => v.venue_id));
    const ordered = [...cheap, ...top.filter((v) => !cheapIds.has(v.venue_id))];

    let taken = 0;
    for (const v of ordered) {
      if (seenIds.has(v.venue_id) || seenNames.has(v.name_key)) continue; // drop chain-branch duplicates
      seenIds.add(v.venue_id);
      seenNames.add(v.name_key);
      chosen.push(v);
      taken += 1;
      if (taken >= perInterest) break;
    }
  }

  return chosen.slice(0, maxCandidates).map((v) => ({
    venue_id: v.venue_id,
    name: v.name,
    category: v.category,
    est_group_cost_php: pyRound(venueCost(v, scenario.group_size)),
    average_rating: v.average_rating,
    operating_hours: v.operating_hours,
    latitude: v.latitude as number,
    longitude: v.longitude as number,
  }));
}

/** Reasons no itinerary from these candidates can pass the checker (empty = may be feasible).
 * Necessary conditions only; opening hours are not considered. */
export function feasibility(candidates: Candidate[], scenario: Scenario, weather: WeatherDay[]): string[] {
  const days = scenario.duration_days;
  const rainDays = weather.filter((w) => !w.is_outdoor_suitable).length;
  const anyOutdoor = weather.some((w) => w.is_outdoor_suitable);
  const reasons: string[] = [];
  if (candidates.length < 3 * days) {
    reasons.push(
      `only ${candidates.length} candidate venues for ${days} day(s); at least ${3 * days} unique venues are needed`
    );
  }
  const indoor = candidates.filter((c) => !c.category.includes("Nature"));
  if (indoor.length < 3 * rainDays) {
    reasons.push(
      `only ${indoor.length} non-Nature candidates for ${rainDays} rainy day(s); at least ${3 * rainDays} are needed`
    );
  }
  const costs = candidates.map((c) => c.est_group_cost_php).sort((a, b) => a - b);
  const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);
  if (costs.length >= 3 && sum(costs.slice(0, 3)) > scenario.budget_php) {
    reasons.push(
      `the 3 cheapest candidates cost PHP ${fmtThousands(sum(costs.slice(0, 3)))} together, ` +
        `over the daily budget of PHP ${fmtThousands(scenario.budget_php)}`
    );
  } else if (costs.length >= 3 * days && sum(costs.slice(0, 3 * days)) > days * scenario.budget_php) {
    reasons.push(
      `the ${3 * days} cheapest candidates cost PHP ${fmtThousands(sum(costs.slice(0, 3 * days)))} together, ` +
        `over the trip budget of PHP ${fmtThousands(days * scenario.budget_php)}`
    );
  }
  for (const i of scenario.interests) {
    if (i === "Nature" && !anyOutdoor) continue; // the checker excuses Nature when every day is rainy
    if (!candidates.some((c) => c.category.includes(i) && (anyOutdoor || !c.category.includes("Nature")))) {
      reasons.push(`no candidate can cover the interest ${i}`);
    }
  }
  return reasons;
}
