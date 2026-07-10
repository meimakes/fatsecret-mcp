# fatsecret-mcp

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/new/template?template=https%3A%2F%2Fgithub.com%2Fmadebydia%2Ffatsecret-mcp)

A Model Context Protocol (MCP) server for the [FatSecret Platform API](https://platform.fatsecret.com/platform-api), with OAuth 1.0a 3-legged user authentication so agents can read and write the authenticated user's food diary.

Comes with an interactive setup command for the OAuth dance and handles a dozen or so FatSecret API quirks (wrong method names, metric-vs-named serving semantics, silent error envelopes, rejected meal values) so you don't have to.

Speaks MCP over **stdio** by default, or **SSE** when run behind `mcp-proxy` (see [Deploy on Railway](#deploy-on-railway)).

## Install

```bash
pipx install fatsecret-mcp          # recommended
# or
pip install fatsecret-mcp
```

Requires Python 3.10+.

## Prerequisites

1. A FatSecret developer account: https://platform.fatsecret.com
2. An app registered in the developer console. On the **API Keys** page, toggle on **REST API OAuth 1.0 Credentials** and copy:
   - Consumer key
   - Consumer secret
3. Add your public IP to the app's IP whitelist (required for OAuth 2.0 client-credentials flow; for OAuth 1.0a user-scoped calls, FatSecret doesn't enforce a whitelist in practice).

OAuth 1.0 credentials are a **separate pair** from the OAuth 2.0 client credentials on the same app. The consumer key is usually the same string, but the secrets differ. Use the OAuth 1.0 pair here.

## Setup

One-time interactive 3-legged OAuth:

```bash
fatsecret-mcp auth
```

You'll be prompted for the consumer key/secret, then shown a URL. Open it in a browser signed in to your **FatSecret user account** (the one your food diary belongs to — not the developer account), click Allow, and FatSecret will show you a numeric PIN. Paste it back into the prompt.

The resulting user token is saved to `~/.config/fatsecret-mcp/config.json` (mode 0600). It doesn't expire; rotate by re-running `auth`.

Verify:

```bash
fatsecret-mcp whoami
```

## Run

```bash
fatsecret-mcp serve
```

Speaks MCP over stdio. Register with any MCP-compatible client:

### Claude Code (or Claude Desktop)

Add to your MCP config:

```json
{
  "mcpServers": {
    "fatsecret": {
      "command": "fatsecret-mcp",
      "args": ["serve"]
    }
  }
}
```

### OpenClaw

```json
{
  "mcp": {
    "servers": {
      "fatsecret": {
        "command": "fatsecret-mcp",
        "args": ["serve"]
      }
    }
  }
}
```

### Direct `python -m`

If you'd rather not rely on the entry-point script:

```json
{
  "command": "/path/to/python",
  "args": ["-m", "fatsecret_mcp", "serve"]
}
```

## Deploy on Railway

For agents that aren't on the same host as the FS config file (a hosted bot, a phone-side agent, multiple gateways sharing one tracker), deploy this as an SSE endpoint:

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/new/template?template=https%3A%2F%2Fgithub.com%2Fmadebydia%2Ffatsecret-mcp)

The button clones the repo, builds the `Dockerfile`, wraps the stdio MCP in [`mcp-proxy`](https://github.com/sparfenyuk/mcp-proxy), and exposes `/sse` on Railway's `$PORT`. **But it can't do the FatSecret OAuth dance for you** — that's interactive (browser + PIN). Run the dance locally first, then paste the four resulting tokens into Railway's env-var prompt.

### Before you click the button

You need four values. The first two come from FatSecret's dev console; the last two come from running the OAuth flow locally.

**1. Register an app on FatSecret** at https://platform.fatsecret.com → API Keys. Toggle on **REST API OAuth 1.0 Credentials**, copy the consumer key + consumer secret.

**2. Run the OAuth flow locally** to get a user-scoped access token for *your* food diary:

```bash
pip install fatsecret-mcp
fatsecret-mcp auth
```

You'll be prompted for the consumer key + secret from step 1, then shown a URL. Open it in a browser signed in to your FatSecret **user account** (the one with your diary, not the dev account), click Allow, and FatSecret shows a numeric PIN. Paste it back. The user-scoped tokens land in `~/.config/fatsecret-mcp/config.json`.

**3. Click the Deploy button.** Railway prompts for the four env vars below — fill from the values you now have.

### Required env vars

| Var | Where to get it |
|-----|-----------------|
| `FATSECRET_CONSUMER_KEY` | step 1 — FatSecret dev console |
| `FATSECRET_CONSUMER_SECRET` | step 1 — same place |
| `FATSECRET_USER_TOKEN` | step 2 — `user_token` field in `~/.config/fatsecret-mcp/config.json` |
| `FATSECRET_USER_TOKEN_SECRET` | step 2 — `user_token_secret` field, same file |

### Connecting from your MCP client

After deploy, Railway gives you a URL like `https://fatsecret-mcp-production-XXXX.up.railway.app`. Wire it into your MCP client:

```json
{
  "mcp": {
    "servers": {
      "fatsecret": {
        "url": "https://fatsecret-mcp-production-XXXX.up.railway.app/sse",
        "transport": "sse"
      }
    }
  }
}
```

### Security posture

This deployment is **single-user**: every caller on the URL writes to the FatSecret diary belonging to whichever user the env-var token came from.

**Bearer auth (recommended).** Set a `MCP_AUTH_TOKEN` env var in Railway. When set, the server requires every request to include `Authorization: Bearer <MCP_AUTH_TOKEN>` and returns 401 otherwise. Generate a token with `python -c "import secrets; print(secrets.token_urlsafe(32))"` and paste into Railway. Your MCP client config then needs:

```json
{
  "url": "https://your-deploy.up.railway.app/mcp",
  "transport": "streamablehttp",
  "headers": { "Authorization": "Bearer <MCP_AUTH_TOKEN>" }
}
```

**Unauthenticated (URL-as-secret).** Skip `MCP_AUTH_TOKEN` and the server runs open. Fine for "all my own agents calling my own diary" if Railway gave you a random-suffixed subdomain; weaker if the subdomain is guessable.

For multi-user / per-caller-token setups, this package isn't the right shape — you'd want a different MCP that accepts a user-token header per call.

### Local Docker

Same image, runnable anywhere:

```bash
docker build -t fatsecret-mcp .
docker run -p 8000:8000 \
  -e FATSECRET_CONSUMER_KEY=... \
  -e FATSECRET_CONSUMER_SECRET=... \
  -e FATSECRET_USER_TOKEN=... \
  -e FATSECRET_USER_TOKEN_SECRET=... \
  fatsecret-mcp
```

Endpoint: `http://localhost:8000/sse`.

## Tools

| Tool | What it does |
|------|-------------|
| `search_food` | FatSecret public DB search by name/brand |
| `get_food` | Full macros + every available serving (returns `serving_id`s) |
| `get_profile` | User's height / weight / goal |
| `get_diary` | Enriched JSON diary entries for one date, with serving details and totals |
| `get_diary_range` | Same enriched entries for an inclusive range of up to 31 days |
| `log_food` | Write an entry to the user's diary |
| `log_amount` | Write an entry using an absolute amount and unit |
| `replace_entry` | Atomically replace an entry's serving/units (and optionally name/meal) |
| `delete_entry` | Remove a diary entry by id |
| `create_custom_food` | Create a custom food (Premier tier only) |

`log_food` takes intuitive `servings` (multiplier of the chosen serving) and the MCP handles the conversion to FS's `number_of_units` semantics internally — see quirks below.

`get_diary` and `get_diary_range` return machine-readable JSON. Every entry
contains `food_id`, `serving_id`, `number_of_units`, the original amount/unit,
the serving and measurement descriptions, metric serving amount/unit, the
scaled metric amount, a `raw_or_cooked` value when FatSecret explicitly
provides one, the food-entry name, and every nutrient returned by the diary
API. Nutrients are available both as direct entry fields and in a `nutrients`
object; the original `macros` object remains for compatibility. Missing
optional nutrients are `null`, not zero.
Serving metadata is fetched from the food record and cached per distinct food
within each request.

Diary nutrient units follow FatSecret's API: calories are kcal; carbohydrate,
protein, fats, fiber, and sugar are grams; cholesterol, sodium, potassium,
vitamin C, calcium, and iron are milligrams; vitamin A is micrograms. Available
fields are `calories`, `protein`, `fat`, `carbohydrate`, `saturated_fat`,
`polyunsaturated_fat`, `monounsaturated_fat`, `cholesterol`, `sodium`,
`potassium`, `fiber`, `sugar`, `vitamin_a`, `vitamin_c`, `calcium`, and `iron`.

`replace_entry` maps directly to FatSecret's `food_entry.edit`, so its serving,
amount, optional meal, and optional name changes happen in one upstream
operation. FatSecret cannot edit an entry's `food_id` or date; changing either
still requires create + delete and therefore cannot be atomic.

## Config resolution

In priority order:

1. Env vars: `FATSECRET_CONSUMER_KEY`, `FATSECRET_CONSUMER_SECRET`, `FATSECRET_USER_TOKEN`, `FATSECRET_USER_TOKEN_SECRET`
2. `$FATSECRET_MCP_CONFIG` file path
3. `$XDG_CONFIG_HOME/fatsecret-mcp/config.json` (default `~/.config/fatsecret-mcp/config.json`)
4. Legacy: `~/.fatsecret_creds` + `~/.fatsecret_user_token` (for users migrating from pre-package script)

## FatSecret quirks this package handles for you

Learned the hard way from production debugging. Don't want anyone else to repeat:

- **Method names are singular**: it's `food_entry.create`, not `food_entries.create`. Plural returns `error 10: Unknown method`.
- **Silent error envelopes**: FS returns HTTP 200 even on API-level failures, with `{"error": {"code": ..., "message": ...}}`. The thin HTTP client in `client.py` raises `FatSecretError` on these so callers don't receive a silent "success" for a failed write.
- **`number_of_units` is NOT a serving multiplier**: it's a count in the serving's own measurement unit. For the "100 g" serving (whose own `number_of_units=100`), sending `0.09` records 0.09 *grams*, not 0.09 servings. For "1 tbsp" (own `number_of_units=1`), it acts like a multiplier. `log_food` transparently multiplies the caller's `servings` by the serving's `number_of_units` to get the correct API value.
- **Meal "Snack" is rejected**: the API only accepts `Breakfast`, `Lunch`, `Dinner`, `Other`. `log_food` normalizes `snack` / `snacks` / `Snack` → `Other`.
- **Response shape for `food_entry.create` is nested**: `{"food_entry_id": {"value": "..."}}`. Not a flat string id.
- **`food_entry_name` is required** on create. `log_food` auto-fills from `food.get.v4` if you don't pass one.
- **OAuth 1.0a rejects Authorization header**: FS only reads OAuth params from query string or POST body — not the `Authorization: OAuth ...` header that RFC 5849 permits. We use body form-urlencoded.
- **request_token must be POST**: the HTTP method is part of the signature base string; a GET with identical params produces a different, invalid signature even if you think OAuth 1.0a is method-agnostic.
- **OAuth 1.0 and OAuth 2.0 have separate credential pairs** on the same app. Same consumer_key string, different secrets. The FS dev console shows them under separate sections.
- **`food_entries.get.v2` returns error 1 on empty diary**: when the requested date has zero entries, FS responds with `code=1, message="unknown error, try again later"` instead of an empty list. Diary tools catch this specific code and return a structured day with an empty `entries` array.
- **Diary reads omit serving metadata**: `food_entries.get.v2` returns the IDs and entry nutrients but not measurement or metric serving fields. Diary tools enrich each entry with its exact serving from `food.get.v4` and cache repeated food lookups.
- **Optional diary nutrients may be absent**: FatSecret omits nutrient fields it does not have rather than returning zero. Diary tools preserve that distinction as `null` and total only the values FatSecret supplied.
- **Entry edits are the only atomic replacement FatSecret supports**: `food_entry.edit` can change serving, units, name, and meal together, but cannot change the food or date. `replace_entry` intentionally exposes that boundary.

## Scope notes

On the free platform tier with a 3-legged user token, these work:

- `foods.search`, `food.get.v4` (public DB reads)
- `profile.get` (user profile)
- `food_entries.get.v2` (user diary read)
- `food_entry.create` / `food_entry.edit` / `food_entry.delete` (user diary writes)
- `weight.update` (weight diary writes)

Premier-only:

- `foods.create` (custom foods)
- Anything marked `*` in the FS API docs

Water tracking is **not exposed** by the FatSecret API at any tier. The mobile app's water widget is app-internal state.

## Tests

```bash
pip install -e '.[dev]'
pytest
```

The unit tests are offline — signature math only. Integration tests against the live FS API aren't included (would require live credentials); use `fatsecret-mcp whoami` as the smoke test after setup.

## License

MIT. See `LICENSE`.
