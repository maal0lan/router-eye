# RouterScope

Private multi-account OpenRouter usage dashboard. FastAPI backend, React + Recharts frontend, PostgreSQL in Docker (SQLite can be used for local backend-only development), encrypted management keys, session login, CSRF protection, and daily historical snapshots.

## Requirements
- Docker and Docker Compose
- One OpenRouter **management key** for each account to connect

## Run locally with Docker Compose
1. Copy `.env.example` to `.env`.
2. Generate secrets:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(48))"  # session secret
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # encryption key
   ```
   The second command requires `cryptography`; alternatively after installing backend requirements, run it inside the backend Python environment.
3. Put unique random values in `ADMIN_PASSWORD`, `SESSION_SECRET`, `ENCRYPTION_KEY`, and `POSTGRES_PASSWORD`. Never commit `.env`.
4. For local development, keep `COOKIE_SECURE=false`. Set `COOKIE_SECURE=true` behind HTTPS in production.
5. Run `docker compose up --build`.
6. Open http://localhost:5173 and sign in with the admin credentials from `.env`.
7. Add accounts using management keys from https://openrouter.ai/settings/management-keys.

The browser UI is Vite dev server for local development. Do **not** expose port 5173 to the public internet as a production frontend. For deployment, build the frontend (`cd frontend && npm install && npm run build`) and serve `frontend/dist` via a static web server or reverse proxy with HTTPS. Proxy `/api/*` to the FastAPI service and configure `CORS_ORIGINS` to the exact public frontend origin. Do not publish the backend or database directly; put the app behind an HTTPS reverse proxy and firewall the database.

## Backend-only development
```bash
cd backend
python -m venv .venv
# activate venv, then:
pip install -r requirements.txt
cp ../.env.example ../.env
# edit ../.env; set DATABASE_URL=sqlite:///./routerscope.db
uvicorn app.main:app --reload --port 8000
```
Run the frontend in another terminal with `cd frontend && npm install && npm run dev`. Set `VITE_API_BASE=http://localhost:8000` in `frontend/.env.local` if needed. `CORS_ORIGINS` must include `http://localhost:5173`.

## Data and API
- `GET /api/health` — health check
- `POST /api/auth/login`, `GET /api/auth/me`, `POST /api/auth/logout` — session auth
- `GET/POST /api/accounts`, `DELETE /api/accounts/{id}` — account management
- `POST /api/refresh` — refresh connected accounts now
- `GET /api/dashboard?days=30` — aggregate summary and daily series
- `GET /api/history?days=90` — stored daily snapshots

The backend calls the official OpenRouter `GET /api/v1/credits`, `GET /api/v1/analytics/meta`, and `POST /api/v1/analytics/query` endpoints. It requests `total_usage`, `request_count`, and `tokens_total` with daily granularity. A background task refreshes every `POLL_MINUTES` (default 15); each refresh also reconciles the latest 30 days of daily analytics into the database. The API can return errors if a connected key lacks management-key access or the upstream analytics schema changes.

## Security notes before public deployment
- Use HTTPS and set `COOKIE_SECURE=true`.
- Use a long unique admin password, session secret, Fernet encryption key, and database password.
- Back up `ENCRYPTION_KEY` securely: if lost, stored keys cannot be decrypted. If rotated, re-encrypt existing keys before deploying the new key.
- Protect `.env` and database backups; never commit them.
- This is a single-admin dashboard. It is not a multi-tenant service. Do not give untrusted people access to the same instance.
- Configure rate limiting at the reverse proxy, monitor logs, keep dependencies updated, and add automated backups before production use.
- Snapshots begin only after the app is running and accounts are connected. Historic OpenRouter data is queried where available; the dashboard does not invent missing local history.
