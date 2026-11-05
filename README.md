# OpportunityOS

OpportunityOS discovers publicly accessible hackathons and internships, extracts evidence backed details, matches them against a personal profile, and presents the results in a dashboard.

## Requirements

- Python 3.11+
- Node.js 20+
- A TinyFish API key
- A Gemini API key
- PostgreSQL for shared or hosted use; SQLite works locally by default

## Configure

Copy `backend/.env.example` to `backend/.env`, then set `TINYFISH_API_KEY` and `GEMINI_API_KEY`. To enable automatic email digests, also set `RESEND_API_KEY`, `EMAIL_FROM` (a verified Resend sender), and `EMAIL_TO`. Email is optional; discovery continues when email is not configured, and the dashboard shows which values are missing. The root `.env.example` lists the same server settings and the optional frontend API URL. `GEMINI_MODEL` defaults to `gemini-2.5-flash`. `DATABASE_URL` defaults to a local SQLite database and accepts a PostgreSQL URL such as `postgresql+psycopg://user:password@localhost:5432/opportunityos`.

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

## Daily automation on GitHub Actions

`.github/workflows/daily-discovery.yml` runs at `06:30 UTC`, which is `12:00 PM Asia/Kolkata`. GitHub-hosted runners run independently of your computer and dashboard. GitHub scheduled events can be delayed during high load, so this is a daily noon schedule rather than a guaranteed exact-minute service.

To activate it, configure repository Actions secrets `TINYFISH_API_KEY`, `GEMINI_API_KEY`, `RESEND_API_KEY`, `EMAIL_FROM`, `EMAIL_TO`, and `DATABASE_URL`. `DATABASE_URL` must point to persistent PostgreSQL shared with the deployed dashboard backend; local SQLite cannot be shared with GitHub-hosted runners. Run `alembic upgrade head` on that database (the workflow also applies migrations), deploy the backend/dashboard against it, then enable Daily Automation in the dashboard. The enable switch is stored in the database and the scheduled runner skips work while disabled. `workflow_dispatch` can be used to manually inspect the external workflow. GitHub concurrency prevents overlapping scheduled jobs.

The schedule is not active until this workflow is present on the repository's default branch and the required GitHub secrets and persistent database are configured. GitHub Actions run history is the source for confirming actual scheduled execution.

## How discovery works

1. The dashboard starts a research run.
2. The backend searches TinyFish independently for hackathon and internship queries across configured public domains.
3. TinyFish Fetch retrieves public result pages; authenticated-only sources are not accessed.
4. Gemini returns schema constrained extraction. Page text remains the evidence, and unsupported fields stay unknown.
5. Application links are retained only when their literal URL occurs in fetched page text. Source URLs come from TinyFish results.
6. Similar titles and organizations are checked before persistence. The run retains raw search, fetch, and extraction output for debugging.
7. Opportunity matches are stored and shown in the dashboard.
8. TinyFish pages are persisted before Gemini extraction. If extraction fails, an explicitly unverified raw-results section is mailed and the pages remain queued with a retry cooldown. Successful recovery links the raw page to an existing or newly saved opportunity.
9. One email digest is generated per run: verified opportunities and pending raw pages can appear together. A repeated run containing only unchanged duplicates does not send another digest; a truly empty search sends an empty report.

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
- `GET` / `PUT /api/automation/status`
- `GET /api/email/status`, `POST /api/email/test`, and `POST /api/email/digest`
- `GET /api/raw-results/status` and `POST /api/raw-results/retry`
- `GET` / `PUT /api/profile`

## Current scope

Email notifications use the Resend API and store each attempt and its included opportunities in the database. Resend's accepted message response is recorded as `sent`; delivery to the recipient's inbox requires provider delivery events or inbox confirmation, which this version does not currently consume. No example opportunities are inserted as discoveries. Deployments should use PostgreSQL and a durable background worker before relying on long-running discovery jobs in production.
