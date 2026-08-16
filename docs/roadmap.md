# Roadmap — phases in strict order, no calendar

Rules: phases run in order; a phase is not started until the previous phase's **Definition of
Done (DoD)** passes; tick checkboxes as tasks complete; the calendar is managed by the user,
never by the assistant.

**Current phase: 3 — complete (DoD met: Arabic spoken into a phone on mobile data, against the
public URL, answered out loud). Next up: Phase 4 — tools, memory, barge-in, personas.**

Live: https://sarjy-kappa.vercel.app · API https://sarjy-api-336308540019.europe-west1.run.app

---

## Phase 0 — Repository setup & credential verification

- [x] Git repo initialized; root `.gitignore` (node_modules/, venv/, **pycache**/, dist/,
      .env, .DS_Store, eval/recordings/); root `README.md` stub (what Sarjy is, layout, how to run — grows later)
- [x] Folder skeleton per CLAUDE.md layout (empty `__init__.py` / `.gitkeep` where needed;
      **no application code yet**)
- [x] `backend/.env.example` listing every variable (empty values):
      DEEPGRAM_API_KEY, GEMINI_API_KEY, GEMINI_MODEL, GEMINI_TTS_MODEL,
      ELEVENLABS_API_KEY, ELEVENLABS_VOICE_AR_EGYPTIAN_MALE/_FEMALE,
      ELEVENLABS_VOICE_AR_GULF_MALE/_FEMALE, ELEVENLABS_VOICE_EN_MALE/_FEMALE,
      VOICE_GENDER, DATABASE_URL, TTS_PROVIDER=gemini, DEFAULT_CITY=Alexandria
      _(six voice slots instead of three per D-024)_
- [x] `backend/requirements.txt` (Phase-0 minimal: python-dotenv, httpx, sqlalchemy,
      psycopg2-binary) + venv created
- [x] `backend/scripts/smoke_test.py` — for each credential in `.env`: verify with a
      zero/near-zero-cost call; print PASS / FAIL(reason+hint) / PENDING(empty var):
      Gemini (list models; print available Flash + TTS model names so the user can fill
      GEMINI_MODEL/GEMINI_TTS_MODEL), Deepgram (auth check), ElevenLabs (list voices +
      print remaining free-quota characters), Supabase (connect, SELECT 1),
      Open-Meteo + Aladhan (keyless reachability; print today's Asr time for Alexandria)
- [x] Human checklist printed/maintained for manual signups (Deepgram, Google AI Studio,
      ElevenLabs, Supabase, Vercel, Google Cloud, UptimeRobot, GitHub remote)
      → `docs/setup-checklist.md`
- [x] First commits pushed to GitHub

**DoD:** `python scripts/smoke_test.py` prints PASS for every credentialed service and both
keyless APIs; repo is on GitHub; `git status` shows no secret files tracked.

## Phase 1 — Text-only brain loop (no audio on purpose)

- [x] SQLAlchemy models for the six tables (product.md §10); tables created in Supabase
      (`scripts/init_db.py`, idempotent)
- [x] Minimal chat: React text box → FastAPI endpoint → Gemini (Egyptian persona system
      prompt) → reply rendered; every turn stored in `messages`
- [x] Language detector (`language.py`, character-ratio rule from product.md §6.5) labeling
      each message _(pytest: 16 cases incl. Arabic, English, code-switched, digits, empty)_

**DoD:** typing "اسمي أحمد وبحب اللون الأزرق" returns a sensible Egyptian-dialect reply and
both rows appear in Supabase with correct language labels.
_(Why text-first: isolates brain+database bugs from audio bugs — one unknown at a time.)_

## Phase 2 — Voice loop + THE go/no-go gate

- [x] WebSocket endpoint; frontend mic capture (MediaRecorder, echoCancellation:true)
      streaming chunks up _(protocol in D-039; half-duplex mic-pause per D-040)_
- [x] Server forwards audio to Deepgram Nova-3 streaming; interim transcripts pushed live
      to UI (dimmed → solid on final) _(**not** `language=multi` — it excludes Arabic; two
      raced connections instead, D-036 / D-042)_
- [x] Final transcript → Phase-1 brain → reply → Gemini TTS → audio streamed down
      sentence-by-sentence → queued playback _(PCM→WAV framing D-037; chunking D-038)_
- [x] **Go/no-go test (D-016):** spoke the code-switch test set live; transcripts pulled from
      `messages`. **Verdict: GO** — full evidence table in D-047. No pivot.

**DoD: met.** A spoken mixed-language question yielded a spoken, correct-language reply
end-to-end locally (four spoken replies heard live); go/no-go verdict logged as D-047.

## Phase 3 — Deploy immediately (yes, half-finished)

- [x] Backend container → Cloud Run (`python:3.14-slim`; timeout 3600s, min-instances 0,
      max 3, 512Mi, session affinity, CPU boost; 21 secrets as env vars, none in the image)
      _(region chosen by measurement, D-049; service shape and platform surprises, D-051)_
- [x] Frontend → Vercel (root directory `frontend/`, Vite, Git integration); CORS/WSS wiring
      _(one allowlist covers CORS **and** `/ws`, which CORS middleware never sees — D-048)_
- [ ] Optional UptimeRobot warm ping — **deliberately deferred.** The cold start is 4s, which
      is annoying rather than broken, so the call is held until the overnight number below is
      in. Not a blocker for Phase 4; revisit before the Phase 6 demo at the latest.
- [x] Fallback rule D-009: **not invoked.** Cloud Run served the voice loop first try; the
      fallback's Pro-plan premise is corrected in D-053.

**DoD: met.** Spoken Arabic on an iPhone, on mobile data, against
https://sarjy-kappa.vercel.app → heard Sarjy reply. Server-side evidence:
`ws: session 8c2e4b0d… open (… audio audio/webm;codecs=opus)` then
`turn: think 1485ms · first audio 7472ms · 1/1 frames`.
The mobile gotcha fired, but not where it was expected: capture was fine (the phone records
WebM/Opus, not AAC) and **playback** was blocked by iOS's per-element autoplay rule — D-050.
_(Deploy problems are their own species of bug; meet them with maximum slack remaining.)_

**Cold-start cost, measured after ~45 minutes idle: `/api/health` took 4.03s cold against
0.22s warm — a ~3.8s penalty**, paid by whoever arrives first and landing on top of their first
turn. (The successful phone test was itself a cold start and still worked, at 7.5s for the
whole turn.) So the UptimeRobot ping is worth adding for the review window: an HTTP monitor on
`/api/health` every ~10 minutes, which is well inside the free tier and keeps one instance warm
without paying for `--min-instances=1`.
**Still do the overnight version before Phase 4:** 45 minutes idle may keep the image warm on
the node, and a genuinely cold pull can be slower. If the overnight number matches 4s, the
ping is optional; if it is much worse, it is not.

## Phase 4 — Tools, memory, barge-in, personas, onboarding

- [x] Four tools wired via function calling (product.md §8), incl. prayer-anchored booking
      _(manual loop, 4-round cap, every call logged; prayer method follows the city's country, D-054)_
- [x] Fact extractor background task (product.md §9); facts injected into system prompt
      _(cheap model, owned by the socket so Cloud Run does not throttle it — D-058)_
- [x] First-use greeting + returning greeting (product.md §5) _(two voices for the bilingual line, D-059)_
- [x] Barge-in per product.md §11; mute button; ~60s silence auto-end _(state machine in D-056)_
- [x] Persona toggle 🇪🇬/🇸🇦 (prompt + voice swap only); persisted to `users.preferred_persona`
- [x] `turn_metrics` timing logger (D-017) _(definitions in D-057)_
- [x] D-044 honesty re-check: **passed**, no prompt patch needed (D-061)
- [ ] Acceptance narrative (product.md §13) on the deployed URL, live microphone

**DoD:** the full acceptance narrative (product.md §13) passes on the **deployed** URL.

## Phase 5 — Deep-dive hardening & the benchmark

- [ ] Record ~30 eval utterances (10 ar / 10 en / 10 mixed; some noisy/fast) + `truth.csv`
- [ ] `eval/run_benchmark.py`: Deepgram vs Web Speech API vs Gemini transcription; Word
      Error Rate via jiwer per group → `eval/results.md` table
- [ ] Persona prompt tuning against a 10-prompt checklist; Gulf output verified by user
- [ ] Mixed-direction (bidi) transcript rendering finished (Unicode isolation)
- [ ] Graceful bilingual rate-limit handling + auto-retry; quick load sanity (several
      simultaneous sessions)

**DoD:** `eval/results.md` exists with real numbers; all §13 steps still pass deployed;
rate-limit path shows the friendly message, not a broken screen.

## Phase 6 — Writeup, video, presentation, submission

- [x] `docs/writeup.md` → PDF: what I built · architecture diagram · API justification
      (2–3 sentences) · language-policy decisions · benchmark table · echo/self-hearing
      story · honest limitations · next steps (incl. MCP line from D-018)
      _(four pages; `docs/architecture.svg` embedded; headline claims fixed in D-072)_
- [ ] Loom (~3 min) following product.md §13, recorded with `TTS_PROVIDER=elevenlabs`
- [ ] 5-minute deck (option: Claude Design) — timing plan: 0:30 framing / 2:00 live demo /
      1:30 deep-dive findings / 1:00 what broke + next; rehearse twice with a timer
- [ ] One-link package (Notion/Google Doc): live demo · GitHub · PDF · Loom; verify every
      link in an incognito window
- [ ] Submit via the Ashby page (never email files)

**DoD:** a stranger opening the single link can try the live demo, read the PDF, watch the
Loom, and browse the repo — all without asking you anything.

---

## Standing risk table

| Risk                                       | Watch signal                            | Fallback                                                            |
| ------------------------------------------ | --------------------------------------- | ------------------------------------------------------------------- |
| Free Arabic TTS sounds robotic             | First Gemini-TTS Arabic reply (Phase 2) | Ration ElevenLabs for demo/Loom; pre-generate demo lines worst-case |
| Code-switching transcription fails         | Go/no-go gate (Phase 2)                 | Pivot deep dive to Latency (D-016) — pipeline reused as-is          |
| Gemini free rate limits under team traffic | Limit noted during Phase 0              | Friendly bilingual error + retry; demo in a quiet window            |
| ElevenLabs quota exhausted early           | Quota printed by smoke test & dashboard | Hard rule: dev never touches it (CLAUDE.md rule 2)                  |
| Cloud Run fights us                        | End of Phase 3                          | All-Vercel fallback (D-009), then don't look back                   |
| Cold start greets a late reviewer          | Cold-visit check after Phase 3          | UptimeRobot ping                                                    |
| Live demo audio failure                    | —                                       | Loom queued in a tab + clips in slides; never demo without a net    |
