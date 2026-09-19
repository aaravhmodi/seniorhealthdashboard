# Carepath frontend

The interface is a React/Vite app designed for the FastAPI contract in `backend/app/schemas.py`.

```sh
cd frontend
npm install
npm run dev
```

It expects these API routes (proxied through `/api` during development):

- `GET /api/seniors/:seniorId`
- `GET /api/seniors/:seniorId/checkins`
- `POST /api/checkins`

Profile setup is currently a frontend onboarding flow. To persist it, FastAPI should expose `POST /api/seniors` accepting the existing `Senior` contract (including its `caregivers` list).

The check-in request follows `CheckInCreate`: `senior_id`, `source`, `language`, and `text`. Until those endpoints are live, the dashboard displays a clearly labeled sample patient and allows a local optimistic check-in preview.

## Future Linq notifications

Linq notifications should remain backend-owned. Once an evaluation reaches the configured caregiver threshold, FastAPI can create the `NotificationReceipt` defined in the contract and publish the existing WebSocket event. The dashboard should show delivery status only; it should never send PHI directly from the browser. Patient reminders for medications, appointments, and follow-ups need their own explicit SMS consent and opt-out fields before FastAPI passes the saved mobile number to Linq.

## Languages

The Vite frontend uses `i18next` and `react-i18next` (rather than `next-translate`, which requires Next.js). Supported UI locales are English, Spanish, Portuguese, Simplified Chinese, and Hindi. FastAPI/voice should return a BCP-47 language code on every check-in; an unsupported code must fall back to English and be shown to the user as unavailable.

## Supabase Auth setup

1. Create a Supabase project and enable Email auth.
2. Copy `.env.example` to `.env.local` and set `VITE_SUPABASE_URL` plus `VITE_SUPABASE_PUBLISHABLE_KEY`.
3. In Supabase Auth settings, add the local and deployed frontend URLs as redirect URLs.

The frontend API client automatically sends the current Supabase access token as an `Authorization: Bearer` header. FastAPI must verify that token before a real deployment treats an API request as authenticated.
