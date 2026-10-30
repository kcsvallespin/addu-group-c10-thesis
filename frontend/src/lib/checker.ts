import { fmtThousands, pyFixed, pyListRepr, pyStr } from "./pycompat";
import type { Candidate, Scenario, WeatherDay } from "./types";

// Port of run_scenario.py's check_output(). Problems are fed back to the model in the repair prompt, so every
// message must match the Python text exactly.

const MIN_VISIT_MIN = 30;
const MAX_VISIT_MIN = 240; // not applied to Accommodation
const DAY_START_MIN = 6 * 60;
const DAY_END_MIN = 23 * 60;
const TRAVEL_SPEED_KMH = 25; // urban Davao assumption; soft travel check only
const TRAVEL_TOLERANCE_MIN = 10;

const DAYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]; // indexed by Date.getUTCDay()

type Json = Record<string, unknown>;

function isObject(x: unknown): x is Json {
  return typeof x === "object" && x !== null && !Array.isArray(x);
}

function haversineKm(a: { latitude: number; longitude: number }, b: { latitude: number; longitude: number }) {
  const toRad = (d: number) => (d * Math.PI) / 180;
  const [la1, lo1, la2, lo2] = [toRad(a.latitude), toRad(a.longitude), toRad(b.latitude), toRad(b.longitude)];
  const h = Math.sin((la2 - la1) / 2) ** 2 + Math.cos(la1) * Math.cos(la2) * Math.sin((lo2 - lo1) / 2) ** 2;
  return 2 * 6371 * Math.asin(Math.sqrt(h));
}

/** int() of one "HH" or "MM" part, as Python accepts it (surrounding whitespace, optional sign). */
function pyInt(s: string): number | null {
  return /^\s*[+-]?[0-9]+\s*$/.test(s) ? parseInt(s, 10) : null;
}

function toMin(hhmm: string): number {
  const parts = hhmm.split(":");
  const h = parts.length === 2 ? pyInt(parts[0]) : null;
  const m = parts.length === 2 ? pyInt(parts[1]) : null;
  if (h === null || m === null) throw new Error(`bad time ${hhmm}`);
  return h * 60 + m;
}

function parseTime(t: unknown): number | null {
  if (typeof t !== "string") return null;
  const parts = t.split(":");
  if (parts.length !== 2) return null;
  const h = pyInt(parts[0]);
  const m = pyInt(parts[1]);
  if (h === null || m === null) return null;
  if (h < 0 || h > 24 || m < 0 || m >= 60) return null;
  return h * 60 + m;
}

function withinHours(hours: Record<string, string> | null, dateStr: string, start: string, end: string): boolean | null {
  if (!hours) return null;
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateStr);
  if (!match) return null;
  const [y, mo, d] = [Number(match[1]), Number(match[2]), Number(match[3])];
  const date = new Date(Date.UTC(y, mo - 1, d));
  if (date.getUTCFullYear() !== y || date.getUTCMonth() !== mo - 1 || date.getUTCDate() !== d) return null;
  const rng = hours[DAYS[date.getUTCDay()]];
  if (rng == null) return null;
  if (rng === "closed") return false;
  const s = toMin(start);
  const e = toMin(end);
  for (const part of rng.split(",")) {
    const bounds = part.trim().split("-");
    if (bounds.length !== 2) throw new Error(`bad hours ${part}`);
    const [oRaw, cRaw] = bounds;
    const o = toMin(oRaw);
    let c = toMin(cRaw);
    if (c <= o) c += 24 * 60; // overnight range
    if (o <= s && e <= c) return true;
  }
  return false;
}

/** run_scenario.py's shape_problem(): null when the checker can walk the reply. */
export function shapeProblem(data: unknown): string | null {
  if (!isObject(data) || !("itinerary" in data)) return "Missing top-level 'itinerary' key";
  if (!Array.isArray(data.itinerary)) {
    return "Itinerary JSON does not match the required shape ('itinerary' must be a list of days)";
  }
  for (const day of data.itinerary) {
    if (!isObject(day)) return "Itinerary JSON does not match the required shape (every day must be an object)";
    if ("day" in day && !(typeof day.day === "number" && Number.isInteger(day.day))) {
      return "Itinerary JSON does not match the required shape ('day' must be a whole number)";
    }
    if ("date" in day && typeof day.date !== "string") {
      return "Itinerary JSON does not match the required shape ('date' must be a \"YYYY-MM-DD\" string)";
    }
    if ("slots" in day && !Array.isArray(day.slots)) {
      return "Itinerary JSON does not match the required shape ('slots' must be a list)";
    }
    if (((day.slots as unknown[] | undefined) ?? []).some((s) => !isObject(s))) {
      return "Itinerary JSON does not match the required shape (every slot must be an object)";
    }
  }
  return null;
}

export interface RouteStats {
  legs: number;
  tightLegs: number;
  km: number;
}

export interface CheckResult {
  problems: string[];
  warnings: string[];
  routeStats: RouteStats;
}

export function checkOutput(data: unknown, candidates: Candidate[], scenario: Scenario, weather: WeatherDay[]): CheckResult {
  const byId = new Map(candidates.map((c) => [c.venue_id, c]));
  const problems: string[] = [];
  const warnings: string[] = [];
  const routeStats: RouteStats = { legs: 0, tightLegs: 0, km: 0 };
  const shape = shapeProblem(data);
  if (shape) return { problems: [shape], warnings: [], routeStats };

  const tripDates = weather.map((w) => w.date);
  const rainByDate = new Map(weather.map((w) => [w.date, !w.is_outdoor_suitable]));
  const daysList = (data as Json).itinerary as Json[];
  if (daysList.length !== scenario.duration_days) {
    problems.push(`Itinerary has ${daysList.length} days (need ${scenario.duration_days})`);
  }

  const used: string[] = [];
  const dayCosts = new Map<string, number>();
  const seenDates: string[] = [];

  for (const day of daysList) {
    for (const key of ["day", "date", "slots"]) {
      if (!(key in day)) problems.push(`Day missing '${key}'`);
    }
    const dn = pyStr(day.day);
    const date = "date" in day ? (day.date as string) : "";
    if (tripDates.length && !tripDates.includes(date)) {
      problems.push(`Day ${dn}: date ${date} is not a trip date ${pyListRepr(tripDates)}`);
    } else if (seenDates.includes(date)) {
      problems.push(`Date ${date} appears more than once`);
    }
    seenDates.push(date);

    const slots = ((day.slots as Json[] | undefined) ?? []);
    if (slots.length < 3 || slots.length > 5) {
      problems.push(`Day ${dn}: ${slots.length} slots (need 3-5)`);
    }

    const timed: Array<[number, number, Candidate]> = []; // (start, end, venue) for the order/overlap/travel checks
    for (const slot of slots) {
      for (const key of ["time_start", "time_end", "venue_id"]) {
        if (!(key in slot)) problems.push(`Slot missing '${key}'`);
      }
      const vid = slot.venue_id;
      const v = typeof vid === "string" ? byId.get(vid) : undefined;
      if (!v) {
        problems.push(`Hallucinated venue_id: ${pyStr(vid)}`);
        continue;
      }
      used.push(v.venue_id);
      dayCosts.set(dn, (dayCosts.get(dn) ?? 0) + v.est_group_cost_php); // the same numbers the model was given

      const sMin = parseTime(slot.time_start);
      const eMin = parseTime(slot.time_end);
      if (sMin === null || eMin === null || eMin <= sMin) {
        problems.push(`${v.venue_id} (${v.name}): invalid time range ${pyStr(slot.time_start)}-${pyStr(slot.time_end)}`);
        continue;
      }
      const timeStart = slot.time_start as string;
      const timeEnd = slot.time_end as string;
      timed.push([sMin, eMin, v]);

      const length = eMin - sMin;
      if (length < MIN_VISIT_MIN || (length > MAX_VISIT_MIN && !v.category.includes("Accommodation"))) {
        problems.push(
          `${v.venue_id} (${v.name}): visit length ${length} min is outside ${MIN_VISIT_MIN}-${MAX_VISIT_MIN} min`
        );
      }
      if (sMin < DAY_START_MIN || eMin > DAY_END_MIN) {
        problems.push(`${v.venue_id} (${v.name}): ${timeStart}-${timeEnd} is outside 06:00-23:00`);
      }
      if (rainByDate.get(date) && v.category.includes("Nature")) {
        problems.push(
          `Weather conflict: ${v.venue_id} (${v.name}) is outdoors on ${date}, which is marked unsuitable for outdoor visits`
        );
      }

      let ok: boolean | null;
      try {
        ok = withinHours(v.operating_hours, date, timeStart, timeEnd);
      } catch {
        ok = null;
      }
      if (ok === false) {
        problems.push(`${v.venue_id} (${v.name}) scheduled outside hours on ${date}`);
      } else if (ok === null) {
        warnings.push(`${v.venue_id} (${v.name}) has unknown hours`);
      }
    }

    // order + overlap (listed order must be chronological)
    for (let i = 0; i < timed.length - 1; i++) {
      const [s1, e1, v1] = timed[i];
      const [s2, , v2] = timed[i + 1];
      if (s2 < s1) {
        problems.push(`Day ${dn}: slots are not in chronological order (${v1.venue_id} then ${v2.venue_id})`);
      } else if (s2 < e1) {
        problems.push(`Day ${dn}: slots overlap (${v1.venue_id} and ${v2.venue_id})`);
      } else {
        // soft travel-time check (straight-line lower bound)
        const km = haversineKm(v1, v2);
        const need = (km / TRAVEL_SPEED_KMH) * 60;
        const gap = s2 - e1;
        routeStats.legs += 1;
        routeStats.km += km;
        if (need - gap > TRAVEL_TOLERANCE_MIN) {
          routeStats.tightLegs += 1;
          warnings.push(
            `travel: day ${dn} ${v1.venue_id}->${v2.venue_id} ${pyFixed(km, 1)} km needs ~${pyFixed(need, 0)} min, ` +
              `gap is ${gap} min (short by ${pyFixed(need - gap, 0)})`
          );
        }
      }
    }
  }

  // Python sorts the day keys as strings; plain < on strings compares the same way for these keys
  for (const [d, cost] of [...dayCosts.entries()].sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0))) {
    if (cost > scenario.budget_php) {
      problems.push(`Day ${d}: estimated cost PHP ${fmtThousands(cost)} exceeds budget PHP ${fmtThousands(scenario.budget_php)}`);
    } else {
      warnings.push(`Day ${d}: estimated cost PHP ${fmtThousands(cost)} (within budget)`);
    }
  }

  const counts = new Map<string, number>();
  for (const v of used) counts.set(v, (counts.get(v) ?? 0) + 1);
  const dupes = [...counts.entries()].filter(([, n]) => n > 1).map(([v]) => v);
  if (dupes.length) problems.push(`Repeated venues: ${pyListRepr(dupes.sort())}`);

  const covered = new Set(used.flatMap((vid) => byId.get(vid)?.category ?? []));
  let missing: string[] = scenario.interests.filter((i) => !covered.has(i));
  if (missing.length) {
    const outdoorDays = weather.filter((w) => w.is_outdoor_suitable);
    if (missing.includes("Nature") && outdoorDays.length === 0) {
      warnings.push("Nature not covered, but the forecast marks every day as unsuitable for outdoor visits");
      missing = missing.filter((m) => m !== "Nature");
    }
    if (missing.length) problems.push(`Interests not covered: ${pyListRepr(missing)}`);
  }

  return { problems, warnings, routeStats };
}
