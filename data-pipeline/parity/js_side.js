// usage: node js_side.js <compiled lib dir> <cases.json>
const path = require("path");
const fs = require("fs");
const lib = process.argv[2];
const { loadCandidates, feasibility } = require(path.join(lib, "lib", "venues.js"));
const { buildPrompt, repairMessage } = require(path.join(lib, "lib", "prompt.js"));
const { checkOutput } = require(path.join(lib, "lib", "checker.js"));
const { weatherFor } = require(path.join(lib, "lib", "weather.js"));
const { parseItineraryJson } = require(path.join(lib, "lib", "openrouter.js"));
const { PyRandom } = require(path.join(lib, "lib", "pycompat.js"));

// Python's json.dump(allow_nan=True) may write NaN; none of our cases need it, so plain JSON.parse is fine
const data = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
let fails = 0;
const fail = (what, a, b) => {
  fails++;
  if (fails <= 15) console.log("MISMATCH", what, "\n  py:", JSON.stringify(a).slice(0, 400), "\n  js:", JSON.stringify(b).slice(0, 400));
};
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

let checks = 0;
for (const c of data.cases) {
  const req = c.req;
  const scenario = { duration_days: req.duration_days, budget_php: req.budget_php, interests: req.interests, group_size: req.group_size };
  const weather = weatherFor(req.weather_pattern, req.duration_days, req.start_date);
  if (!eq(weather, c.expect.weather)) fail("weather " + JSON.stringify(req), c.expect.weather, weather);
  const cands = loadCandidates(scenario);
  if (!eq(cands, c.expect.candidates)) fail("candidates " + JSON.stringify(req), c.expect.candidates.map((x) => x.venue_id), cands.map((x) => x.venue_id));
  const prompt = buildPrompt(scenario, cands, weather);
  if (prompt !== c.expect.prompt) {
    let i = 0;
    while (prompt[i] === c.expect.prompt[i]) i++;
    fail("prompt " + JSON.stringify(req), c.expect.prompt.slice(i - 40, i + 60), prompt.slice(i - 40, i + 60));
  }
  const f = feasibility(cands, scenario, weather);
  if (!eq(f, c.expect.feasibility)) fail("feasibility", c.expect.feasibility, f);
  c.itins.forEach((it, k) => {
    checks++;
    const res = checkOutput(it, cands, scenario, weather);
    const exp = c.expect.checks[k];
    if (!eq(res.problems, exp.problems)) fail("problems " + JSON.stringify(it).slice(0, 200), exp.problems, res.problems);
    else if (!eq(res.warnings, exp.warnings)) fail("warnings", exp.warnings, res.warnings);
    else if (!eq([res.routeStats.legs, res.routeStats.tightLegs], exp.route)) fail("route", exp.route, res.routeStats);
    else if ((res.problems.length ? repairMessage(res.problems) : "") !== exp.repair) fail("repair", exp.repair, repairMessage(res.problems));
  });
}

data.raws.forEach((t, i) => {
  let got;
  try {
    got = { ok: true, value: parseItineraryJson(t) };
  } catch {
    got = { ok: false };
  }
  if (!eq(got, data.parse[i])) fail("parse " + JSON.stringify(t), data.parse[i], got);
});

[1, 2, 3, 5, 10, 33, 100, 1000].reduce((rng, n, i) => {
  const x = Array.from({ length: n }, (_, k) => k);
  rng.shuffle(x);
  if (!eq(x, data.shuffles[i])) fail("shuffle " + n, data.shuffles[i], x);
  return rng;
}, new PyRandom(42));

console.log(`${data.cases.length} requests, ${checks} checker runs, ${data.raws.length} parse cases: ${fails} mismatch(es)`);
process.exit(fails ? 1 : 0);
