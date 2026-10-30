export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

interface OpenRouterResponse {
  choices?: Array<{
    message?: { content?: string };
    finish_reason?: string;
  }>;
  usage?: { prompt_tokens?: number; completion_tokens?: number };
  error?: unknown;
}

const MAX_RETRIES = 4;
const RETRY_STATUS = new Set([429, 500, 502, 503, 504]);

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** POST to OpenRouter. Retries dropped connections and temporary server errors with
 * backoff; any other non-200 response (bad key, no credits, retired model) throws immediately. */
export async function callModel(model: string, messages: ChatMessage[], temperature: number): Promise<OpenRouterResponse> {
  const apiKey = process.env.OPENROUTER_API_KEY;
  if (!apiKey) throw new Error("OPENROUTER_API_KEY is not set on the server");

  let lastError: string | null = null;
  for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
    if (attempt > 0) {
      const wait = 5 * 2 ** (attempt - 1);
      await sleep(wait * 1000);
    }
    let resp: Response;
    try {
      resp = await fetch("https://openrouter.ai/api/v1/chat/completions", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${apiKey}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ model, messages, temperature }),
        signal: AbortSignal.timeout(15 * 60 * 1000),
      });
    } catch (e) {
      lastError = e instanceof Error ? e.name : "RequestError";
      continue;
    }
    if (RETRY_STATUS.has(resp.status)) {
      lastError = `HTTP ${resp.status}`;
      continue;
    }
    if (resp.status !== 200) {
      const text = await resp.text();
      throw new Error(`OpenRouter error ${resp.status}: ${text}`);
    }
    try {
      return (await resp.json()) as OpenRouterResponse;
    } catch {
      lastError = "empty or invalid response body";
    }
  }
  throw new Error(`Giving up after ${MAX_RETRIES} retries: ${lastError}`);
}

export function getContent(data: OpenRouterResponse): { text: string; finishReason: string | null } {
  const choice = data.choices?.[0];
  if (!choice) {
    return { text: "", finishReason: `no choices (${JSON.stringify(data.error ?? null).slice(0, 200)})` };
  }
  return { text: choice.message?.content ?? "", finishReason: choice.finish_reason ?? null };
}

/** Port of run_scenario.py's parse_json(). Throws when the reply is not a JSON value. */
export function parseItineraryJson(text: string): unknown {
  if (!text || !text.trim()) throw new Error("empty response");
  // some reasoning models (e.g. DeepSeek R1) inline their reasoning
  let cleaned = text.replace(/<think>[\s\S]*?<\/think>/g, "").trim();
  if (cleaned.startsWith("```")) {
    cleaned = cleaned.replace(/^`+/, "").replace(/`+$/, "");
    if (cleaned.toLowerCase().startsWith("json")) cleaned = cleaned.slice(4);
  }
  cleaned = cleaned.trim();
  if (!cleaned.startsWith("{") && cleaned.includes("{") && cleaned.includes("}")) {
    // prose before/after the JSON object
    cleaned = cleaned.slice(cleaned.indexOf("{"), cleaned.lastIndexOf("}") + 1);
  }
  return JSON.parse(cleaned);
}
