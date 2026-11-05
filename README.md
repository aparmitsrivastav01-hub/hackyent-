# OpportunityOS

OpportunityOS discovers publicly accessible hackathons and internships, extracts evidence backed details, matches them against a personal profile, and presents the results in a dashboard.

## Requirements

- Python 3.11+
- Node.js 20+
- A TinyFish API key
- A Gemini API key
- PostgreSQL for shared or hosted use; SQLite works locally by default

## Configure

Copy `backend/.env.example` to `backend/.env`, then set `TINYFISH_API_KEY` and `GEMINI_API_KEY`. The root `.env.example` lists the same server settings and the optional frontend API URL. `GEMINI_MODEL` defaults to `gemini-2.5-flash`. `DATABASE_URL` defaults to a local SQLite database and accepts a PostgreSQL URL such as `postgresql+psycopg://user:password@localhost:5432/opportunityos`.

## Run locally

Terminal 1, from `backend/`:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
```

Open `http://localhost:8000/docs` for the API documentation. The API creates missing tables on startup for convenience; use the Alembic migration for repeatable schema upgrades.

Terminal 2, from the repository root:

```powershell
npm install
npm run dev
```

Open `http://localhost:3000`. Set `NEXT_PUBLIC_API_URL` if the API runs somewhere other than `http://localhost:8000`.

## How discovery works

1. The dashboard starts a research run.
2. The backend searches TinyFish independently for hackathon and internship queries across configured public domains.
3. TinyFish Fetch retrieves public result pages; authenticated-only sources are not accessed.
4. Gemini returns schema constrained extraction. Page text remains the evidence, and unsupported fields stay unknown.
5. Application links are retained only when their literal URL occurs in fetched page text. Source URLs come from TinyFish results.
6. Similar titles and organizations are checked before persistence. The run retains raw search, fetch, and extraction output for debugging.
7. Opportunity matches are stored and shown in the dashboard.

The Search and Fetch implementations follow TinyFish's current public API documentation: `GET https://api.search.tinyfish.ai` and `POST https://api.fetch.tinyfish.ai`, authenticated using `X-API-Key`. Gemini uses Google's `google-genai` SDK structured response schema. API keys are optional at startup; discovery returns a clear configuration error until both are set.

## Tests

From `backend/`:

```powershell
pytest
```

Tests mock external services and do not call TinyFish or Gemini.

## API summary

- `GET /api/health`
- `GET /api/opportunities` with type, location, work mode, status, minimum score, sort, and pagination filters
- `GET /api/opportunities/{id}` and `/match`
- `PATCH /api/opportunities/{id}/review`
- `POST /api/discovery/run`
- `POST /api/discovery/run/{run_id}/status`
- `GET /api/research/runs`
- `GET /api/dashboard/stats`
- `GET` / `PUT /api/profile`

## Current scope

Email, scheduling, automatic applications, and form submissions are intentionally out of scope. No example opportunities are inserted as discoveries. Deployments should use PostgreSQL and a durable background worker before relying on long-running discovery jobs in production.
