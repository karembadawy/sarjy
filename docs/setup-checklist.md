# Manual signup checklist (human-only steps)

Everything here is done by hand in a browser. Each item ends with **exactly what to copy**
and **which `backend/.env` variable it goes into**. After each item, run:

```bash
cd backend && venv/bin/python scripts/smoke_test.py
```

Rules that apply throughout: free tier only, never enable a paid plan, and never paste a
key anywhere except `backend/.env` (gitignored).

---

## Needed now (Phase 0 — the smoke test checks these)

### 1. Deepgram — speech recognition

- [ ] Sign up at **console.deepgram.com** (free signup credit, no card).
- [ ] Left sidebar → **API Keys** → *Create a New API Key*. Name it `sarjy-dev`,
      permission **Member**, expiration: never.
- [ ] **Copy the key immediately** — it is shown exactly once.
- → `DEEPGRAM_API_KEY=`

The smoke test prints your remaining credit balance, so you can watch it barely move.

### 2. Google AI Studio — the brain (Gemini) and default voice

- [ ] Go to **aistudio.google.com/apikey**, sign in with your Google account.
- [ ] *Create API key* → pick a Google Cloud project (or let it make one).
- [ ] Copy the key.
- → `GEMINI_API_KEY=`
- [ ] Leave `GEMINI_MODEL` and `GEMINI_TTS_MODEL` **empty for now**. Run the smoke test —
      it prints every Flash and TTS model your key can actually see. Copy two names from
      that list (golden rule 7: never guess model names).
- → `GEMINI_MODEL=` (a Flash model, e.g. one of the `gemini-*-flash*` names it prints)
- → `GEMINI_TTS_MODEL=` (one of the `*-tts` names it prints)

Stay on the **free** tier — do not enable billing on the AI Studio key's project.

### 3. ElevenLabs — demo-only voices

Free quota is ~10,000 characters/month (~10 minutes of speech). It is rationed for the
Loom recording and the live demo. Development always uses Gemini TTS (`TTS_PROVIDER=gemini`).

- [ ] Sign up at **elevenlabs.io** (free plan).
- [ ] Avatar menu → **API Keys** → create a key. Permissions needed now: `user: read`
      and `voices: read`. (Add `text_to_speech` too — Phase 6 needs it for the recording.)
- → `ELEVENLABS_API_KEY=`
- [x] Pick six voices — one male and one female per language slot (D-024). In **Voice
      Library**, filter language = **Arabic**, then *Add to my voices*. For every voice:
      **My Voices** → the voice's **⋮** menu → *Copy Voice ID*.

      | Variable                              | Chosen             | Label              |
      | ------------------------------------- | ------------------ | ------------------ |
      | `ELEVENLABS_VOICE_AR_EGYPTIAN_MALE`   | Masry              | ar, egyptian       |
      | `ELEVENLABS_VOICE_AR_EGYPTIAN_FEMALE` | Yasmine            | ar, egyptian       |
      | `ELEVENLABS_VOICE_AR_GULF_MALE`       | Karim              | ar, modern standard |
      | `ELEVENLABS_VOICE_AR_GULF_FEMALE`     | Suhair Al Abtah    | ar, modern standard |
      | `ELEVENLABS_VOICE_EN_MALE`            | Spuds Oxley        | en, american       |
      | `ELEVENLABS_VOICE_EN_FEMALE`          | Cassidy            | en, american       |

- [x] `VOICE_GENDER=female` — which set of the six Sarjy speaks with by default.

> **Quota warning:** the library's built-in sample clips are pre-rendered and free.
> Typing *your own* text into the preview box generates audio and **spends your quota** —
> don't do it. Judge voices from the stock samples only.
>
> Shortcut: once the API key is in, the smoke test prints every voice in your account with
> its ID and language labels — you can copy the IDs from the terminal instead of the site.

### 4. Supabase — PostgreSQL

- [ ] Sign up at **supabase.com** → *New project*.
- [ ] Name `sarjy`, region **EU (Frankfurt)** or **EU (Ireland)** — closest free region to
      Egypt and to the Cloud Run region we'll use in Phase 3.
- [ ] **Save the database password shown at creation** — it is displayed once. (If you lose
      it: Project Settings → Database → *Reset database password*.)
- [ ] Top bar → **Connect** → tab **Session pooler** (host contains `pooler.supabase.com`,
      port `5432`). Copy that URI and replace `[YOUR-PASSWORD]` with your real password.
- → `DATABASE_URL=postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres`

> **Not** the "Direct connection" URI: it is IPv6-only and Cloud Run cannot reach it (D-020).
> If your password contains `@ # : / ?`, percent-encode it (`@`→`%40`, `#`→`%23`, `:`→`%3A`).
>
> **Do not keep the square brackets.** `[YOUR-PASSWORD]` is a placeholder including its
> brackets — typing your password *inside* them sends `[hunter2]` as the password and
> Postgres answers `password authentication failed`, which reads like a wrong password.
> The smoke test now detects this case by name.
>
> Free projects **pause after ~7 days of inactivity**. Keep the project awake during the
> review window — the Phase 3 UptimeRobot ping will handle it once the backend touches the DB.

### 5. GitHub — the remote

- [ ] The remote is already configured: `git@github.com:karembadawy/sarjy.git`.
      Make sure that repository **exists** on GitHub (create it empty — no README, no
      .gitignore, no license — so the first push is clean).
- [ ] Decide **public** (reviewers open one link) or private-now/public-later.
- No `.env` variable.

---

## Needed later (create the accounts now, wire them up in Phase 3+)

### 6. Vercel — frontend hosting (Phase 3)

- [ ] Sign up at **vercel.com** with **GitHub** (so the repo is importable in one click).
- [ ] Nothing to configure yet. In Phase 3 we import `sarjy` and set
      **Root Directory = `frontend/`**.
- No `.env` variable. (The frontend gets a `VITE_WS_URL` in Phase 3, set in Vercel's UI.)

### 7. Google Cloud — backend hosting on Cloud Run (Phase 3)

- [ ] Sign up at **console.cloud.google.com** and create a project named `sarjy`.
- [ ] Enable **Billing** with a card. Cloud Run needs a verified billing account; we stay
      inside the always-free allowance, so the bill stays $0. (Set a **$1 budget alert**
      anyway: Billing → Budgets & alerts.)
- [ ] Enable these APIs: **Cloud Run**, **Artifact Registry**, **Cloud Build**.
- [ ] Install the CLI locally: `brew install --cask google-cloud-sdk`, then run
      `gcloud auth login` yourself (type `! gcloud auth login` here and it runs in this session).
- No `.env` variable — Phase 3 sets the same variables as Cloud Run service env vars.

### 8. UptimeRobot — cold-start ping (Phase 3, optional)

- [ ] Sign up at **uptimerobot.com** (free: 50 monitors, 5-minute interval).
- [ ] Nothing to configure until the Cloud Run URL exists.
- No `.env` variable.

---

## Progress

| # | Service          | Variable(s)                                                          | Done |
| - | ---------------- | -------------------------------------------------------------------- | ---- |
| 1 | Deepgram         | `DEEPGRAM_API_KEY`                                                   | ☑    |
| 2 | Google AI Studio | `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_TTS_MODEL`                 | ☑    |
| 3 | ElevenLabs       | `ELEVENLABS_API_KEY`, the six `ELEVENLABS_VOICE_*`, `VOICE_GENDER`   | ☑    |
| 4 | Supabase         | `DATABASE_URL`                                                       | ☑    |
| 5 | GitHub           | —                                                                    | ☑    |
| 6 | Vercel           | — (Phase 3)                                                          | ☐    |
| 7 | Google Cloud     | — (Phase 3)                                                          | ☐    |
| 8 | UptimeRobot      | — (Phase 3)                                                          | ☐    |

Chosen model names (verified present on the key, D-025):
`GEMINI_MODEL=gemini-3.7-flash` · `GEMINI_TTS_MODEL=gemini-3.1-flash-tts-preview`
