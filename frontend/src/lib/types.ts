export type Interest =
  | "Food"
  | "Nature"
  | "Culture"
  | "Entertainment"
  | "Shopping"
  | "Wellness"
  | "Accommodation"
  | "Sightseeing";

export type WeatherPattern = "clear" | "mixed" | "rain" | "live";

export type ModelChoice = "main" | "baseline";

/** One row of data/venues.json (= data-pipeline/benchmark_venues.json, built from price_data.xlsx). */
export interface Venue {
  venue_id: string;
  place_id: string;
  name: string;
  name_key: string;
  category: Interest[];
  price_unit: "per_person" | "room_night";
  price_php: number;
  price_source: string;
  price_date: string;
  average_rating: number | null;
  rating_count: number | null;
  quality_score: number;
  operating_hours: Record<string, string> | null;
  latitude: number | null;
  longitude: number | null;
  address: string | null;
}

export interface Candidate {
  venue_id: string;
  name: string;
  category: Interest[];
  est_group_cost_php: number;
  average_rating: number | null;
  operating_hours: Record<string, string> | null;
  latitude: number;
  longitude: number;
}

export interface WeatherDay {
  date: string;
  condition: string;
  pop: number;
  is_outdoor_suitable: boolean;
}

export interface Scenario {
  duration_days: number;
  budget_php: number;
  interests: Interest[];
  group_size: number;
}

export interface GenerateRequest {
  duration_days: number;
  budget_per_person_php: number;
  group_size: number;
  interests: Interest[];
  weather_pattern: WeatherPattern;
  start_date: string; // YYYY-MM-DD
  model: ModelChoice;
}

export interface Slot {
  time_start: string;
  time_end: string;
  venue_id: string;
}

export interface ItineraryDay {
  day: number;
  date: string;
  slots: Slot[];
}

export interface Itinerary {
  itinerary: ItineraryDay[];
}

export interface AttemptLog {
  attempt: number;
  problems: string[];
  warnings: string[];
  passed: boolean;
  finish_reason: string | null;
}

export interface GenerateResponse {
  itinerary: Itinerary | null; // null when the last reply was not valid JSON
  data_version: string;
  candidates: Candidate[];
  weather: WeatherDay[];
  scenario: Scenario;
  attempts: AttemptLog[];
  final_passed: boolean;
  model_used: string;
  prompt_tokens: number;
  completion_tokens: number;
  seconds: number;
}

export interface GenerateError {
  error: string;
}
