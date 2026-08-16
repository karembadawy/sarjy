# Sarjy · سرجي

A bilingual (Egyptian Arabic + English) voice assistant. Tap once, then talk hands-free.
Sarjy understands Arabic and English — even code-switched inside one sentence — replies by
voice in the matching language and dialect, remembers facts about you across sessions, and
can check the weather, look up Islamic prayer times, and book appointments (say
"بكرة بعد العصر" and it resolves Asr through a prayer-times API before booking).

Take-home project for [Sarj.ai](https://sarj.ai).

## Live

| | |
| --- | --- |
| **Try it** | **https://sarjy-kappa.vercel.app** — tap the orb, allow the microphone, talk |
| API health | https://sarjy-api-336308540019.europe-west1.run.app/api/health |

Works on a phone. Microphone access needs HTTPS, which both hosts provide. Sarjy is
deliberately half-finished at this point: it listens, thinks and speaks, and that is all —
tools, memory, personas and barge-in arrive in Phase 4 (see `docs/roadmap.md`).

## Layout

```
backend/     FastAPI + Uvicorn, one WebSocket endpoint (Python 3.14, venv in backend/venv)
  app/       pipeline: speech recognition → brain → voice synthesis
  scripts/   one-off scripts (smoke_test.py verifies every credential)
frontend/    React + Vite + Tailwind, single screen
eval/        speech-recognition benchmark (recordings are gitignored)
docs/        product.md (what) · decisions.md (why) · roadmap.md (order)
```

## Stack

React + Vite + Tailwind on Vercel · FastAPI on Google Cloud Run · Deepgram Nova-3
(multilingual streaming speech recognition) · Google Gemini Flash with function calling ·
Gemini TTS / ElevenLabs / browser voice router · Supabase PostgreSQL via SQLAlchemy ·
Open-Meteo and Aladhan for weather and prayer times.

## Running it

Backend setup (Python 3.11+; this repo is built on 3.14):

```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # then fill in your own keys
python scripts/smoke_test.py
```

`scripts/smoke_test.py` checks every credential with a zero-cost call and prints
PASS / FAIL / PENDING per service. It never generates speech, so it never burns quota.

Then run the two halves:

```bash
cd backend  && venv/bin/python -m uvicorn app.main:app --reload   # API + /ws on :8000
cd frontend && npm install && npm run dev                          # UI on :5173
```

To check the voice loop without a microphone — it streams a synthesised Arabic utterance into
the socket and fails unless a transcript, a reply and audio all come back:

```bash
cd backend && venv/bin/python scripts/ws_smoke.py
```

## Deploying

Backend → Cloud Run (project `sarjy-260816`, region `europe-west1` — chosen by measurement,
see D-049):

```bash
cd backend
venv/bin/python scripts/cloudrun_env.py --set ALLOWED_ORIGINS='https://sarjy-kappa.vercel.app,*.vercel.app'
gcloud run deploy sarjy-api --source . --region=europe-west1 --allow-unauthenticated \
  --timeout=3600 --min-instances=0 --max-instances=3 --memory=512Mi --cpu=1 \
  --session-affinity --cpu-boost --env-vars-file=.env.cloudrun.yaml
venv/bin/python scripts/ws_smoke.py --url wss://<service-url>/ws   # verify
```

`cloudrun_env.py` turns `backend/.env` into a Cloud Run `--env-vars-file`, printing variable
names only — no key ever reaches a command line, a shell history or a log. The generated file
is gitignored, and `backend/.gcloudignore` keeps `.env` out of the uploaded build context.

Frontend → Vercel: connected to this repo with root directory `frontend/`, framework Vite, and
`VITE_WS_URL` pointing at `wss://<cloud-run-url>/ws`. **Every push to `main` redeploys it** —
there is no frontend deploy command.

## Documentation

- [`docs/product.md`](docs/product.md) — features, UX, language policy, personas, demo script
- [`docs/decisions.md`](docs/decisions.md) — every locked decision and its rationale
- [`docs/roadmap.md`](docs/roadmap.md) — phases 0–6 with definitions of done
