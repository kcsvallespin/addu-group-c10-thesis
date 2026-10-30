import type { WeatherDay, WeatherPattern } from "./types";

/** Fixed, reproducible forecast per pattern - mirrors batch_runner.py's weather_for(),
 * rather than calling a live weather API, so results are comparable across model runs. */
export function weatherFor(pattern: WeatherPattern, durationDays: number, startDate: string): WeatherDay[] {
  // UTC arithmetic: a local-midnight Date shifts to the previous day in toISOString() east of UTC (Davao is +8)
  const [y, m, dd] = startDate.split("-").map(Number);
  const days: WeatherDay[] = [];
  for (let d = 0; d < durationDays; d++) {
    let rainy: boolean;
    if (pattern === "clear") rainy = false;
    else if (pattern === "rain") rainy = true;
    else rainy = d % 2 === 1; // mixed: clear, rain, clear, ...

    const date = new Date(Date.UTC(y, m - 1, dd + d));
    days.push({
      date: date.toISOString().slice(0, 10),
      condition: rainy ? "Rain" : "Clear",
      pop: rainy ? 1.0 : 0.1,
      is_outdoor_suitable: !rainy,
    });
  }
  return days;
}

interface OpenWeatherForecastEntry {
  dt: number;
  pop?: number;
  weather: Array<{ main: string }>;
}

interface OpenWeatherForecastResponse {
  city?: { timezone?: number };
  list?: OpenWeatherForecastEntry[];
}

/** Local calendar date (YYYY-MM-DD) for a Unix timestamp in a city at a fixed UTC offset,
 * matching run_scenario.py's `datetime.fromtimestamp(dt, utc) + timedelta(seconds=tz)`. */
function localDateStr(epochSeconds: number, tzOffsetSeconds: number): string {
  return new Date((epochSeconds + tzOffsetSeconds) * 1000).toISOString().slice(0, 10);
}

/** Live 5-day/3-hour forecast for Davao City, collapsed to one entry per date, starting
 * tomorrow in local time - ports run_scenario.py's get_weather(). OpenWeatherMap's free
 * endpoint only covers ~5 days ahead, so this throws if durationDays can't be fully covered. */
export async function getLiveWeather(durationDays: number): Promise<WeatherDay[]> {
  const apiKey = process.env.OPENWEATHERMAP_API_KEY;
  if (!apiKey) throw new Error("OPENWEATHERMAP_API_KEY is not set on the server");

  const resp = await fetch(
    `https://api.openweathermap.org/data/2.5/forecast?q=${encodeURIComponent("Davao City,PH")}&appid=${apiKey}&units=metric`
  );
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`OpenWeatherMap error ${resp.status}: ${text}`);
  }
  const data = (await resp.json()) as OpenWeatherForecastResponse;
  const tz = data.city?.timezone ?? 8 * 3600; // seconds from UTC; Davao = +8h
  const todayLocal = localDateStr(Date.now() / 1000, tz);

  const days = new Map<string, { date: string; condition: string; pop: number }>();
  for (const entry of data.list ?? []) {
    const date = localDateStr(entry.dt, tz);
    if (date <= todayLocal) continue; // plan from tomorrow, in local time
    const main = entry.weather?.[0]?.main ?? "Clear";
    const cur = days.get(date) ?? { date, condition: main, pop: 0 };
    cur.pop = Math.max(cur.pop, Number(entry.pop ?? 0));
    if (main === "Rain") cur.condition = "Rain";
    days.set(date, cur);
  }

  const result = [...days.values()].slice(0, durationDays);
  if (result.length < durationDays) {
    throw new Error(
      `OpenWeatherMap's forecast only covers ${result.length} day(s) from tomorrow; reduce the trip duration or use a fixed weather pattern instead.`
    );
  }
  return result.map((d) => ({ ...d, is_outdoor_suitable: d.pop < 0.5 }));
}
