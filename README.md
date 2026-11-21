# LLM Chatbot with Key Failover and Response Caching

React + FastAPI + SQLite chatbot built on **LiteLLM**. It keeps multi-turn history per session,
fails over between API keys automatically, caches responses, and shows per-request metadata
(source, model, latency, tokens) next to every reply.

## 1. Live URLs

| Part | URL |
|------|-----|
| Repository | https://github.com/Nivesh12345/lyftrai-chatbot |
| Frontend | Pending public deployment |
| Backend | Pending public deployment |
| Health | Pending public deployment |
| Demo video | Pending 2–3 minute recording |

Replace the pending deployment URLs with the public URLs before final submission.
For deployment, Vercel or Netlify is suitable for the frontend and Render is suitable for the backend.

## 2. Run locally

### With Docker
```bash
cp .env.example .env      # then put 2+ real keys in LLM_API_KEYS
make up                   # frontend http://localhost:3000, backend http://localhost:8000
make down
```

Add the second key in the project-root `.env` file only:

```dotenv
LLM_API_KEYS=first-real-key,second-real-key
```

The keys must be separate, comma-separated values, for example `key_1` and `key_2`.
Never add real keys to `.env.example`, commit them to Git, or place them in the
`frontend` environment. `.env` is ignored by Git.

### Without Docker
```bash
# backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export LLM_API_KEYS="key-one,key-two" DATABASE_URL="sqlite:///./app.db" \
       ENABLE_FAILURE_SIMULATION=true ALLOWED_ORIGINS=http://localhost:3000
uvicorn app.main:app --port 8000

# frontend (new terminal)
cd frontend
npm install
VITE_API_URL=http://localhost:8000 npm run dev      # http://localhost:3000
```

### Tests
```bash
make test        # or: cd backend && pip install -r requirements-dev.txt && pytest
```
Tests replace `litellm.completion` with a fake, so they need no API keys or network.
The backend test suite currently contains 21 passing tests covering caching, fallback,
validation, history, token metadata, CORS, and simulated provider failures.

## 3. Design decisions

**Key fallback.** `LLM_API_KEYS` is parsed into `key_1, key_2, ...` (in order). For each request
the backend calls `litellm.completion(api_key=...)` with the first configured model and key. `LLM_MODELS`
is an ordered comma-separated model pool (or it falls back to `LLM_MODEL`). Any failure (429, quota,
5xx, timeout, empty answer) moves on to the next key, then the next model. `fallback_attempts` counts the combinations that failed
first. If every key fails the API returns `503 {"detail": "all providers unavailable"}` and nothing
is written to history. Keys are never logged or returned; only the id (`key_1`) is exposed.
`num_retries=0` so LiteLLM does not waste time retrying a key that is already failing.

**Caching.** LiteLLM's built-in cache (`litellm.cache`) is enabled at startup (in-memory by default,
`CACHE_TYPE=disk` stores it in the `/data` volume). LiteLLM builds the cache key from the model plus the
full message list (system prompt + history + new message), and the API key is not part of it. So the
same first message in a new session is a hit, and any different message is a miss. A hit is detected
through LiteLLM's `cache_hit` flag, reported as `LLM_CACHE`, and returns the same text and the token usage
stored with the cached response (`key_id: null`, `fallback_attempts: 0`).

**SQLite session storage.** The client sends only the new message. The backend loads the session's
history from SQLite, builds the full prompt, and after a *successful* answer writes the user and
assistant rows in one transaction, so failed requests (422/503) never enter history. Schema
(`messages` as specified, plus an index and a small `sessions` table for the session's system prompt) is
created on startup. The file lives at `DATABASE_URL` (`/data/app.db`) on a named Docker volume.

**Other choices.** One uvicorn worker (the in-memory cache is per process). Token counts come
from the provider's usage for that single request. Simulated-failure requests skip the cache so they
always return a real live answer from the next key.

The assistant UI supports Markdown, GFM tables and lists, fenced code blocks, and inline/display
math rendered with KaTeX.

## 4. Verification

Replace `$API` with the backend URL.

```bash
API=http://localhost:8000
curl $API/health

# Live call, then cache hit from a NEW session (same first message)
curl -s $API/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"demo-1","message":"What is the capital of France?"}'
curl -s $API/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"demo-2","message":"What is the capital of France?"}'   # LLM_CACHE, key_id null

# Failover: key_1 "returns 429" for this request only -> answered by key_2, fallback_attempts 1
curl -s $API/chat -H 'Content-Type: application/json' -H 'X-Simulate-Failure: key_1=429' \
  -d '{"session_id":"demo-3","message":"Tell me a fun fact about owls."}'

# Every key fails -> 503, nothing stored
curl -si $API/chat -H 'Content-Type: application/json' -H 'X-Simulate-Failure: *=429' \
  -d '{"session_id":"demo-4","message":"hello"}'
curl -s $API/sessions/demo-4/messages        # 404 session not found

# Validation -> 422
curl -si $API/chat -H 'Content-Type: application/json' -d '{"session_id":"x","message":""}'

# Stored history with per-message token metadata
curl -s $API/sessions/demo-1/messages
```
In the UI, each assistant reply shows source, model, latency and input/output tokens for that
request only (plus the key id and fallback count).

## 5. Setup Used

- Editor: Visual Studio Code
- AI tools: GitHub Copilot SDK / Copilot coding agent

## 6. Bonus features

- Optional per-session system prompt field in the UI (stored per session, sent to the model).
- Disk cache option (`CACHE_TYPE=disk`) so cached answers survive container restarts.
- Docker healthcheck, non-root backend image, and a `make test` target.

## Deployment notes

**Backend (Render):** New Web Service → connect the repo → Runtime *Docker* → Root Directory `backend`.
Set `LLM_API_KEYS`, `ALLOWED_ORIGINS` (your frontend URL, no trailing slash), `ENABLE_FAILURE_SIMULATION=true`,
`DATABASE_URL=sqlite:////data/app.db`, and `CACHE_TYPE=local`. Health check path: `/health`.
On a free plan the disk is ephemeral; history survives container restarts locally via the Docker volume, and
on Render if you attach a persistent disk mounted at `/data`.

**Frontend (Vercel/Netlify):** Root Directory `frontend`, build `npm run build`, output `dist`,
environment variable `VITE_API_URL=https://<your-backend>.onrender.com` (set it *before* building).
