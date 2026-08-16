# Decision Log

Format: what we decided · why · what we rejected. Append new entries (D-018+) as significant
implementation choices are made. Never silently reverse a locked decision — add a superseding
entry explaining why.

---

**D-001 · Deep dive = Bilingual Arabic + English.**
Sarj's moat is Arabic-first voice AI; the builder is a native Egyptian Arabic speaker with 10
years in Saudi Arabia — an unfair advantage no generic candidate can match. Rejected: Latency
(strong but the crowded default choice — kept as pivot, see D-016), Guardrails (weak live
demo), UI/UX (crowded bet), multistep workflows (kept only as flavor, see D-013).

**D-002 · Architecture = modular pipeline (speech recognition → brain → voice synthesis) over one WebSocket.**
Full control and observability of each stage; components swappable; the same pipeline serves
the Latency pivot if needed. Rejected: single speech-to-speech API (e.g. Gemini Live) — less
plumbing but a black box with less to show, defend, and benchmark.

**D-003 · Speech recognition = Deepgram Nova-3, multilingual (code-switching) mode, streaming.**
Best measured Arabic accuracy/speed in production tests; explicit mixed-language-in-one-
utterance support; $200 free signup credit dwarfs project needs; provides endpointing.
Browser Web Speech API kept as benchmark baseline, not runtime dependency.

**D-004 · Brain = Google Gemini Flash (AI Studio free API key), function calling, no agent framework.**
Fast, strong Arabic incl. dialects, free tier sufficient; direct API calls keep the pipeline
readable (presentation material). Rejected: LangChain-style frameworks (opacity, bloat);
OpenAI Realtime (no meaningful free tier).

**D-005 · Voice synthesis = provider router: Gemini TTS (default, dev) | ElevenLabs (demo only) | browser (emergency).**
Gemini TTS is free and Arabic-capable for daily work; ElevenLabs free tier (~10 min/month)
has the best Arabic voices — rationed exclusively for the Loom recording and live demo via
explicit `TTS_PROVIDER=elevenlabs`. Router = 1-line provider swap; also an architecture
talking point.

**D-006 · Database = Supabase-hosted PostgreSQL via SQLAlchemy, from day one, for dev and prod.**
Free hosting wipes local files, so SQLite would lose memories on every restart; one identical
database everywhere removes deploy-day surprises; Supabase dashboard doubles as a live demo
prop. Rejected: SQLite (ephemeral on our hosts), running our own Postgres (pointless ops).

**D-007 · External APIs = Aladhan (prayer times) + Open-Meteo (weather); both keyless and free.**
Written justification (required by the brief): prayer times are the scheduling primitive of
daily life for Sarj's actual customer base — "بكرة بعد العصر" is how their users genuinely
express time, and resolving it requires this API. Weather is the familiar secondary.

**D-008 · Hosting = Vercel (frontend, subdir `frontend/`) + Google Cloud Run (backend container).**
Cloud Run: proven long-lived WebSocket support, scale-to-zero inside always-free allowance,
professional deploy story. Config: request timeout high (up to 60 min), min-instances 0,
optional UptimeRobot warm ping during review week. Requires card-verified Cloud Billing
($0 within free limits; user's Google AI Pro sub may add Developer Program credits).

**D-009 · Hosting fallback = all-Vercel (Vercel Functions WebSocket beta, Python/FastAPI supported since Jul 2026; user's Pro plan raises the connection cap to 30 min).**
Decision rule: if Cloud Run is not serving the deployed voice loop by the end of the deploy
phase, switch to Vercel and don't look back. Primary stays Cloud Run because a weeks-old
beta is the wrong foundation for a hiring demo.

**D-010 · One monorepo (`sarjy`): backend/, frontend/, eval/, docs/.**
Reviewers open exactly one link and see the whole story (eval/ + docs/ = instant rigor
signal); cross-cutting changes land as single coherent commits; both hosts natively deploy
from subdirectories. Rejected: two repos (split narrative, half-seen, zero benefit solo).

**D-011 · Identity = frontend-generated UUID in localStorage; no authentication.**
Auth would cost a build-day for zero evaluation credit. Name captured conversationally on
first use (Sarjy speaks first, asks the name; stored via the normal fact extractor — no
schema addition, and the answer's language seeds the mirror preference). Stated limitation:
memory is per-browser.

**D-012 · Interaction = call-style: one tap, then hands-free with endpointing + barge-in.**
Matches Sarj's phone-agent DNA; effortless demo. Cost accepted: the self-hearing problem,
mitigated by browser echoCancellation + a ≥2-word interim-transcript threshold for barge-in

- demo volume hygiene (see product.md §11).

**D-013 · Booking = exactly one tool call (`create_booking`) + `list_bookings`. No workflow engine.**
Gives the Sarj-flavored money-shot utterance without diluting the deep dive. The brief:
"one done well beats three done shallowly."

**D-014 · Facts stored in canonical English snake_case regardless of input language.**
This single normalization is what makes memory work across languages, not just across time.
`source_language` recorded per fact for the demo/writeup.

**D-015 · Visual identity = Arabic-futuristic; Tailwind CSS.**
Dark minimal canvas, one warm accent, Arabic-first typography (Cairo / IBM Plex Sans Arabic).
Jarvis _energy_ (speed, wit, barge-in) without Iron Man branding — "the Arab Jarvis" is
memorable to this panel; a Marvel homage is not. Rejected: literal sci-fi theming, and
strictly-corporate styling (the brief itself jokes with Clippy).

**D-016 · Go/no-go gate: the first working voice loop must pass a code-switching transcription test.**
Spoken test set incl. "عايز أعمل book لميتنج بكرة الساعة خمسة". Pass → bilingual confirmed.
Fail → pivot deep dive to Latency immediately; the already-built pipeline is exactly what
Latency needs, so nothing is wasted.

**D-017 · Log per-turn timing metrics even though latency is not the deep dive.**
~20 lines of code buys "median response 1.4s, here's the stage breakdown" for the inevitable
Q&A question. `turn_metrics` table.

**D-018 · Function calling directly; MCP explicitly not used.**
Our four tools are internal; an MCP server adds a moving part for zero benefit at this scale.
Writeup line locked: "next step: expose the booking tools as an MCP server so any agent can
use them."

**D-019 · Python 3.14.3 (pyenv), virtualenv at `backend/venv`.**
The only interpreter already installed above the 3.11 floor, and all four Phase-0 dependencies
ship native cp314 wheels (no compiler, no `pg_config`), so setup is one `pip install`. The venv
lives inside `backend/` next to the code it serves and is gitignored. Rejected: system Python
3.9.6 (below the floor), compiling a second pyenv version for no gain.

**D-020 · `DATABASE_URL` = Supabase **Session pooler** URI (IPv4, port 5432), not the direct connection.**
Supabase's direct connection resolves to IPv6 only, and Cloud Run has no IPv6 egress — using it
would pass locally and fail on deploy day, the worst possible failure timing. Session pooling
(not transaction pooling) keeps full session semantics, so SQLAlchemy behaves normally. The
smoke test warns when the configured URL is not a pooler URI. Rejected: direct connection
(breaks in Phase 3), transaction pooler on 6543 (no prepared statements / session state).

**D-021 · Smoke test calls read-only endpoints exclusively, and reports PASS / FAIL(+hint) / PENDING with exit codes 0 / 1 / 2.**
Credential verification must never itself consume the quota it is verifying: ElevenLabs is
checked via `/v1/user/subscription` and `/v1/voices`, never `/text-to-speech`; Gemini via
`ListModels`, never `generateContent`. Every FAIL carries a one-line fix hint so a red run is
self-service. Distinct exit codes (2 = merely unfilled) let a later CI step tell "broken" from
"not configured yet". The script reads `backend/.env` with `dotenv_values` rather than the
process environment, so a stale shell export cannot mask a wrong file.

**D-022 · Prayer times use Aladhan calculation method 5 (Egyptian General Authority of Survey); city→lat/lon uses Open-Meteo's geocoding API.**
Method 5 is the authority Egyptians actually see on TV and in mosque timetables, so Sarjy's Asr
matches the user's expectation to the minute — the whole point of the "بكرة بعد العصر" demo.
Gulf persona will need method 4 (Umm al-Qura) when that path is built. Open-Meteo's geocoder is
keyless and from the same vendor as the forecast, so the weather tool needs no second provider.

**D-023 · Manual signup steps live in `docs/setup-checklist.md`, not in chat scrollback.**
Phase 0 credentials get re-created (expired keys, new machine, a reviewer reproducing the
project); a maintained file with the exact copy-this → paste-into-that mapping makes that a
five-minute job instead of an archaeology exercise.

**D-024 · Six ElevenLabs voice slots (`ELEVENLABS_VOICE_<LANG|DIALECT>_<GENDER>`), one male + one female per slot, selected by `VOICE_GENDER` (default `female`).**
Supersedes the three-variable naming in the Phase 0 roadmap checklist. Recording both genders
costs nothing (voice IDs are free to hold) and buys a same-day answer if a voice turns out to
sound wrong on Arabic — swap a variable instead of re-doing library research mid-demo-prep.
Default female: the Egyptian female voice (Yasmine) is trained as a banking phone agent, which
is precisely Sarj's product register. Chosen: Masry/Yasmine (Egyptian), Karim/Suhair (Gulf
slot), Spuds Oxley/Cassidy (English).

**D-025 · `GEMINI_MODEL=gemini-3.7-flash`, `GEMINI_TTS_MODEL=gemini-3.1-flash-tts-preview`, both read off ListModels rather than recalled.**
Newest non-preview Flash for the brain (best dialect Arabic), newest TTS for development voice.
Pinned to explicit versions, not the floating `gemini-flash-latest` alias: a hiring demo must
behave identically at rehearsal and at presentation. Fallbacks if free-tier limits bite or
Arabic TTS disappoints: `gemini-3.5-flash` and `gemini-2.5-flash-preview-tts`.

**D-026 · The Gulf persona uses Modern-Standard-Arabic-labelled voices (Karim, Suhair) rather than khaleeji-labelled ones.**
No genuinely Gulf-labelled voice was available in the free library. Accepted because dialect
comes from Gemini's generated *text* (golden rule 9), while the voice contributes accent and
timbre only — an MSA-trained voice reading Gulf-dialect text reads as neutral Arabic, not as
wrong Arabic. Revisit in Phase 5 if the user's ear (10 years in Saudi Arabia) rejects it;
the fix is a one-variable swap by construction (D-024).

---

**D-027 · Phase-1 HTTP handlers are sync `def`, not `async def`.**
FastAPI runs sync endpoints in a threadpool, so blocking SQLAlchemy and a blocking Gemini
call are already off the event loop — for zero async plumbing. Keeping `brain.generate_reply`
synchronous also keeps the pipeline readable, which is the point of golden rule 6. Phase 2's
WebSocket is async and will call the same functions via `asyncio.to_thread`. Rejected: async
SQLAlchemy + asyncpg (a second driver, a second set of failure modes, no benefit at this scale).

**D-028 · Gemini call shape: `thinking_level=LOW`, automatic function calling disabled, retry only on 429/503.**
`gemini-3.7-flash` rejects `MINIMAL` with a 400 ("Thinking level MINIMAL is not supported for
this model"), so LOW is the floor — and it measures at zero thinking tokens for conversational
turns, which is what a voice assistant needs. AFC is off because Phase 1 has no tools; it keeps
the call to one round trip. The free tier returned 503 "experiencing high demand" twice in the
first dozen calls, so the brain retries those (and 429) up to twice with 0.6s/1.8s backoff — a
dead turn becomes a slightly slow one. The *user-facing* bilingual rate-limit message stays in
Phase 5 where the roadmap put it.

**D-029 · Tailwind v4 is configured in CSS (`@theme` in `src/index.css`), not in a `tailwind.config.js`.**
Tailwind 4 is a Vite plugin whose supported configuration surface is the CSS `@theme` block;
a JS config file is legacy-compat only. So the visual identity tokens of product.md §4 live as
custom properties in one file, next to the base styles that use them. Same tokens, same
utilities (`bg-ink-950`, `text-amber`, `rounded-bubble`), one fewer file.

**D-030 · Constrained columns use CHECK constraints, not PostgreSQL ENUM types; tables are created with `create_all`, not migrations.**
`role`, `language`, `status` and `preferred_persona` get the same guarantee from a CHECK as
from an ENUM, but adding a value later is one ALTER rather than a type migration. `scripts/init_db.py`
is idempotent `create_all` — right for a six-table schema locked before any code was written,
and honest about its limit: it creates missing tables and notices nothing else. If a column ever
has to change, it changes in the Supabase SQL editor (or Alembic arrives then, not now).
Note: the ORM class for `sessions` is named `ChatSession`, because `Session` is SQLAlchemy's.

**D-031 · One env reader: `app/config.py`, file first then `os.environ`.**
No module calls `os.getenv` directly, so every "you forgot to fill in X" failure reads the same
and carries its own fix hint. It prefers `backend/.env` (a stale shell export must not mask a
wrong file — same reasoning as the smoke test, D-021) and falls through to the real environment,
which is exactly what Cloud Run needs in Phase 3, where secrets are injected as env vars and no
`.env` is shipped.

**D-032 · Persona prompts are built from bilingual ✅/❌ example pairs plus an explicit cross-dialect ban list.**
Abstract instructions ("use Egyptian dialect") drift back to Modern Standard Arabic within a few
turns; concrete "say this, not that" pairs in Arabic do not. Verified in practice: the first
Egyptian build greeted with "يا هلا" — a Gulf phrase — so each persona now also names the *other*
persona's vocabulary as forbidden, not only MSA. After the fix the same prompt answered
"وعليكم السلام! إزيك، عامل إيه؟" and "تمام يا باشا". The rules are written in English (models
follow English instructions more reliably) with the examples in Arabic, which is why the strings
are long: they are the product, not boilerplate.

**D-033 · The locked ratio rule labels the canonical code-switch utterance `ar`, and we keep the rule.**
"عايز أعمل book لميتنج" is 14 Arabic letters against 4 Latin → ratio 0.78, above the 0.70
threshold of product.md §6.5, so it is labelled `ar`, not `mixed`. Arabic words are short, so
most real code-switching by Egyptians lands above the threshold. This is asserted in the tests
so it is a decision on record rather than a demo-day surprise. Kept because §6.5 is locked and
because the *reply* language it produces is right either way; a separate helper,
`dominant_language()`, answers "which language do we reply in" (§6.1) independently of the badge.
Open question for the user: if the `mixed` badge is meant to be the deep dive's visible proof of
code-switching, the rule needs a "contains ≥1 word of each script" override — that is a change to
a locked decision and therefore theirs to make, not ours.

**D-034 · `GEMINI_MODEL=gemini-3.5-flash`, superseding the `gemini-3.7-flash` pin in D-025.**
Measured, not guessed: `gemini-3.7-flash` allows **20 requests per day** on the free tier
(`quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier, quotaValue: 20`). Phase 1's own
probes plus the first browser test exhausted it inside one evening — unusable for building a
voice loop, let alone rehearsing a demo. D-025 pre-authorised exactly this fallback ("if free-tier
limits bite"), so the trigger has simply fired. Egyptian dialect quality is indistinguishable:
3.5-flash returned the same idiom ("عاشت الأسامي") as 3.7-flash on the identical prompt.
Quota is **per model**, so a stuck day is always one variable swap away; `gemini-3.6-flash` and
`gemini-3.5-flash-lite` were both verified working as further fallbacks, and `gemini-2.5-flash`
is 404 on this key. Re-check the daily ceiling before the demo — if 3.5-flash also proves tight,
Phase 6 rehearsals should run against a different model than the live demo.

**D-035 · A 429 is reported as 429 with its real cause, and a per-day quota is never retried.**
Google returns the same 429 for a per-minute burst limit and a spent daily allowance; only the
quotaId (`…PerDay…`) tells them apart. Retrying a daily quota with a 1.8s backoff just stalls
and then lies, so the brain now raises `BrainQuotaError` immediately and the API answers HTTP 429
with "خلصت حصة النهاردة من Gemini المجانية · Free Gemini quota for today is used up." instead of
the blanket 502 used for real breakage. This is debuggability, not the designed rate-limit state
— the polished bilingual screen with auto-retry stays in Phase 5 where the roadmap put it.

**D-036 · Speech recognition = two Deepgram connections raced per utterance (`ar` + `en`), superseding the `language=multi` half of D-003.**
Deepgram's multilingual code-switching mode **does not include Arabic**. The docs list the
`multi` set as English, Spanish, French, German, Hindi, Russian, Portuguese, Japanese, Italian
and Dutch; the January 2026 changelog adds Arabic as a *monolingual* model only. Measured on
synthesised speech before a line of pipeline code was written:

| said | `language=multi` | `language=ar` | `language=en` |
| --- | --- | --- | --- |
| عايز أعمل book لميتنج بكرة الساعة خمسة | `I examel booked limiting booked last time.` (0.61) | **عايز اعمل بوك لميتنج بكره الساعه خسه** (0.99) | _(empty)_ |
| ابعتلي فاتورة الكهربا بكرة الصبح لو سمحت | `ए बातली फ़ातूर तिल काहराबा…` (Devanagari!) | **ابعت لي فاتوره الكهرباء بكره الصبح لو سمحت** | _(empty)_ |
| احجزلي ميعاد عند الدكتور بكرة بعد العصر | `Ehgislimáad an de Dr. Bukrabadelácer.` (0.58) | **احجز لي ميعاد عند الدكتور بكرة بعد العصر** (0.99) | _(empty)_ |
| Can you book me a table for four people tonight? | correct (1.00) | _(empty)_ | correct (1.00) |
| Remind me to call مامتي after Maghrib | `Remind me to call momti after maghreb.` | رمايد ميديك هول مامتي افتر مغرب (0.78) | `Remind me to call after Maghreb.` (1.00) |

So `multi` was never an option, and no *single* setting covers the product: `ar` nails Arabic
and code-switching but returns nothing for English, and vice versa. We therefore open one
connection per language, fan every audio chunk to both, and pick per utterance —
**non-empty beats empty; if both spoke, higher confidence wins, length breaking ties.** That
rule decided all five test utterances correctly and is unit-tested with these exact numbers.
Cost is 2× Deepgram minutes, which is noise against the $200 signup credit (D-003).
Bonus that fell out of it: `ar` writes borrowed English words in Arabic script ("book" →
"بوك"), which is precisely what product.md §6.4 asks the replies to do.
Rejected: `multi` as specified (produces Devanagari for Egyptian Arabic — a guaranteed false
NO-GO at the D-016 gate); `ar` alone (breaks the English half of a bilingual assistant).

**D-037 · Gemini TTS returns raw PCM and we add the WAV header ourselves; the SDK's `audio/wav` response format is a lie for this model.**
`google-genai` 2.18.1 types `AudioResponseFormatParam.mime_type` as accepting `audio/wav`,
which would have saved the framing code. The API answers `400 Audio mime_type is not supported
in response_format`, and the newer `client.interactions.create` shape additionally rejects the
documented `speech_config` object ("Expected an array, got object"). The path that works is the
one ListModels advertises for this model — `models.generate_content` with
`response_modalities=["AUDIO"]` — which returns `audio/l16; rate=24000; channels=1`: headerless
16-bit mono PCM that a browser will not play. So `voice.pcm_to_wav()` prepends the 44-byte
header server-side. Written down because the SDK's own type hints point the other way.

**D-038 · Gemini TTS free tier allows ~10 requests *per day per model*, so a reply is chunked for the ear *and* for the quota, and the model list is a fallback chain.**
Measured: `gemini-3.1-flash-tts-preview` returns 429 with
`quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier, quotaValue: 10`. An early probe
appeared to recover after 70s, which briefly suggested a per-minute throttle — that was quota
accounting lag, and the honest reading is the one the quotaId states: **ten requests a day**,
same shape as D-034's discovery on the brain. Three consequences, all implemented:

1. **Chunking is quota-aware.** Every spoken chunk is one request, so a naive
   sentence-per-frame split burns a day in three turns. `voice.split_for_speech()` splits on
   sentence boundaries — including the Arabic terminators ؟ ؛ ۔ that a Latin-only splitter
   walks straight past — then **merges anything under 60 characters into its neighbour**, so
   "تمام. حجزتلك الميعاد بكرة الساعة أربعة ونص." costs one request, not two, while a long
   reply still streams its first sentence early.
2. **Quota is per model, so the model list is a chain.** `GEMINI_TTS_MODEL_FALLBACKS` is walked
   on a 429 — and on a model that answers with no audio at all, which the 2.5 TTS previews do
   (`finish_reason=OTHER`) on some inputs. We do *not* sleep on the retryDelay: it is ~60s and
   the day's allowance is not coming back anyway, so another model is the only real retry.
3. **The user is told the truth.** Exhaustion shows the bilingual "صوت سرجي واقف شوية" line with
   the reply text still on screen, rather than silence.

Consequence for rehearsal, and the reason D-005 rationed ElevenLabs from the start: free Gemini
TTS cannot carry a demo. Budget roughly 10 spoken chunks per model per day for development, and
record the Loom with `TTS_PROVIDER=elevenlabs`. **Re-check before Phase 6.**

**D-039 · The `/ws` protocol: JSON control frames, binary audio both ways, one connection per call.**
Up: `{type:"hello", user_id, session_id, persona}` → `{type:"ready"}`, then raw MediaRecorder
chunks as binary, `{type:"bye"}` to hang up. Down: `interim` → `final` (with our language label)
→ `reply_text` → `speak_start` → WAV frames → `speak_end`, plus `error` carrying **both**
`message_en` and `message_ar` so no failure path can show a stack trace or a monolingual
apology. Binary frames need no envelope because their meaning is positional — they only occur
between `speak_start` and `speak_end`. `speak_end` is sent from a `finally`, including when
synthesis failed halfway, because it is what tells the browser to un-pause the microphone;
skipping it strands the call in "speaking" forever.

**D-040 · Half-duplex by pausing the microphone during playback, at both ends.**
The browser pauses `MediaRecorder` for the whole of Sarjy's reply and resumes on `speak_end`;
the server independently drops any final that resolves while a turn is in flight. Two belts
because they fail differently — the client one removes the echo path entirely, the server one
survives a client that ignores the protocol. This is deliberately *not* barge-in: product.md
§11 wants interruption, and Phase 4 replaces both halves with the ≥2-word interim trigger.
Marked with TODOs at all three sites so it cannot be mistaken for the finished behaviour.

**D-041 · Deepgram needs a KeepAlive heartbeat, or half-duplex kills the second turn.**
Deepgram closes a live connection after 10 seconds without audio (`NET-0001`). D-040 pauses
the microphone for exactly as long as Sarjy is speaking, which routinely exceeds that on a
three-sentence reply — the first turn would work, then the socket would be dead and the app
silently deaf. The transcriber now sends `{"type":"KeepAlive"}` on every channel every 4s
(docs say 3–5s) for the life of the session. Found by watching both channels close with code
1000 the moment the test audio stopped, not by reading ahead.

**D-042 · The race window adapts to confidence, and empty finals never enter the race.**
Two bugs, one lesson — the channels do not finish together, and the losing channel is loud.
(1) An *empty* final from the silent channel used to count as "this channel has reported",
which closed the race early: measured, the Arabic channel's empty final let a 0.55-confidence
English mis-hear ("Anasrgi,") win a sentence whose real transcript arrived 134ms later. Silence
is not a turn, so empty candidates are now dropped before the race sees them.
(2) The remaining skew between channels ran to 510ms, wider than the original flat 400ms
window. Rather than tax every turn with a 900ms wait, the window now depends on what arrived
first: **250ms if it is ≥0.90 confident, 900ms if it is not.** A confident transcript is very
unlikely to be beaten; a doubtful one usually is. `UtteranceEnd` is likewise demoted to a pure
stall-breaker that only fires 1.5s after the fact if nothing else flushed the buffer — flushing
on it directly caused the same garbage-wins-the-race failure.

**D-043 · Gemini API moves to paid Tier 1 on a $15 credit, superseding "free tier only" in CLAUDE.md golden rule 2.**
The free tier's ceiling is **per model per day**: 20 requests for the brain, 10 for TTS. That is
not a constraint you engineer around, it is a wall — it ended a live go/no-go session after one
scripted utterance, and no amount of chunking or model-chaining fixes an allowance that small.
Measured unit economics made the trade obvious: ~1,900 input + ~60 output tokens per brain turn
($0.0017) and ~6s of audio per reply ($0.0038 on `gemini-3.1-flash-tts-preview` at a measured
**32 audio tokens/second**, or $0.0015 on `gemini-2.5-flash-preview-tts` at 25 tok/s) — about
**half a cent per spoken turn**, so the $15 credit is ~2,700 turns, comfortably more than the
whole remaining project including reviewers hitting the deployed demo.
Scope of the reversal is deliberately narrow: Gemini only. Deepgram stays on its $200 signup
credit, Supabase/Vercel/Cloud Run stay free tier, and the ElevenLabs rationing rule (D-005,
~10 min/month reserved for the Loom and live demo) is untouched. Billing was already required
for Cloud Run in Phase 3 (D-008), so this added no new setup step.
Cost discipline that replaces the old rule: don't loop TTS in scripts, and use the cheaper TTS
model when audio quality is not the thing under test.

**D-044 · `GEMINI_MODEL=gemini-3.7-flash`, restoring D-025 and retiring the D-034 downgrade.**
D-034 moved the brain to `gemini-3.5-flash` for one stated reason — 3.7-flash allowed 20 requests
a day — and D-025 had pre-authorised exactly that fallback. Paid tier removes the trigger, so the
downgrade retires with it. Re-measured against the real persona prompt before switching back, and
3.7-flash is not merely equal but **2–4× faster**: 857–1025ms per turn against 2279–3334ms for
3.5-flash, on identical input, with the Egyptian idiom intact ("وعليكم السلام! إزيك، عامل إيه؟").
That latency is the difference between a voice assistant and a form submission, and it explains
the 8–13s think times logged during the free-tier gate attempt — those were congestion, not the
model. `gemini-3.6-flash` sat between the two and is the first fallback if 3.7 ever misbehaves.
Noted, not fixed: on "عايز أعمل book لميتنج بكرة الساعة خمسة" all three models now answer
"تمام، حجزتلك الميتنج" — claiming a booking they cannot make, since `create_booking` does not exist
until Phase 4. Free-tier 3.5-flash had answered honestly ("معنديش طريقة أحجزلك ميتنج دلوقتي").
This is the honesty clause of SPOKEN_STYLE_RULES losing to a plausible-sounding action, and it
stops being a lie the moment Phase 4 wires the tool — which is why it is recorded here rather
than patched with prompt text that Phase 4 would immediately undo. Re-check it at the end of
Phase 4; if it survives having real tools, it needs a prompt fix then.

**D-045 · The race is decided on expected-correct-words (confidence × word count), not confidence alone.**
Found on a live microphone, not in a probe. Asked something in Egyptian, the Arabic channel
returned the whole eight-word sentence ("طيب ااا ينفع تشوفي لي صرت بعصر لبكرة ساكن") while the
English channel returned the two-word fragment "Type, infarct" — and **the fragment won**, because
0.83 was a larger number than the Arabic channel's confidence. Sarjy then explained what a
myocardial infarction is. Deepgram's confidence is roughly a per-word accuracy, so multiplying it
by word count approximates how much real content a channel actually captured, which is the thing
we are choosing between. Scores on the failure: Arabic 6.3, English 1.66. Length still breaks an
exact tie so "No." never loses to an equally-confident "لأ". Validated against every measured
case — five synthetic and four live — including the ones where English rightly wins.
Also fixed the logging that made this hard to diagnose: the race now records *every* channel's
confidence and score, not just the winner's text.

**D-046 · One spoken utterance is one turn: a channel that finalises after the turn is answered is stale (epoch guard).**
The user said "Remind me to call Dina after Maghrib" **once** and Sarjy answered it **twice**. The
Arabic channel finalised at 05:03:32.445 and won; the turn ran and the microphone un-paused at
05:03:38.145; the English channel finalised the *same sentence* 562ms later and, finding no race
in progress, started a fresh one. The half-duplex mic-pause (D-040) is what stretches the gap:
the losing channel gets no audio during playback, so it cannot reach its endpoint until the mic
resumes — by which time the user has already been answered.
Fix: the transcriber keeps an epoch that increments the instant a race is decided. A channel
stamps its in-progress utterance with the epoch when it first hears anything, and a submission
carrying a stale epoch is dropped rather than raced; all channels' buffers are also cleared on
resolve. Clearing buffers alone was not enough — the late `speech_final` message carries its own
text and would have re-populated an empty buffer, which is why the guard is on the epoch and not
on the buffer. Covered by an async regression test that fails if the guard is removed.

**D-047 · Go/no-go gate (D-016): GO. Bilingual deep dive confirmed; no pivot to Latency.**
Run live on the builder's own microphone, Phase-2 loop, Deepgram nova-3 two-channel racer.
Transcripts below are the exact `messages` rows, not log scrollback.

| # | Spoken | Deepgram (winning channel) | Meaning |
| --- | --- | --- | --- |
| 1 | عايز أعمل **book** لميتنج بكرة الساعة خمسة | `ar` أيزعمل **بوكينج** لميتينج بوكرا الساعة خمسة | **intact** |
| 2 | ايه ال **weather** بكرة في اسكندرية؟ | `ar` هو **الوذر** بوكلا في **إسكندرية** يكون عملي | **intact** |
| 3 | Remind me to call **دينا** after Maghrib | `en` Remind me to call **dinner** tomorrow after **my rib** | structure intact, **entities lost** |
| 3b | _(same utterance, Arabic channel)_ | `ar` ريميند ميتو كول **دينا** تمورو أفضل **مارب** | entities **kept** |
| 4 | الجو النهارده عامل ايه | `ar` الجو النهارده عامل ايه | **exact** |
| 5 | آه يا ريت | `ar` آه يا ريت | **exact** |
| 6 | بقولك هو صلاح النهارده لعب مع … في تركيا | `ar` same | **intact** |

**Verdict: GO.** D-016's bar is "mixed utterances essentially correct, minor spelling variance
fine, meaning intact". Both code-switched utterances cleared it, and the proof is not the
spelling but the behaviour downstream: on #1 the brain parsed a booking request and answered it,
on #2 it answered about the weather in Alexandria tomorrow. Pure Egyptian was transcribed
*exactly* twice. The deep dive stands; the Latency pivot is not taken.

What actually makes it work is the thing the plan did not anticipate: the monolingual Arabic
model writes borrowed English in Arabic script — `book`→بوكينج, `weather`→الوذر — which is
exactly what product.md §6.4 asks the *replies* to do. Code-switching survives as Arabic rather
than being torn between two alphabets.

**The one honest weakness, recorded rather than smoothed over:** an English sentence containing
Arabic proper nouns loses them. "Dina"→"dinner" and "Maghrib"→"my rib" on the channel that won.
The Arabic channel kept both (دينا, مارب) and Gemini read straight through its transliteration —
so the *better* transcript lost the race. Not over-fitted to now: it is one utterance, and
`eval/` in Phase 5 is where this gets quantified across ~30 recordings instead of guessed at.
Two candidate fixes for then: bias the race toward the channel that preserves named entities, or
feed the brain both transcripts and let it choose.

Not scoreable: one turn whose spoken original the builder could not recall. It is excluded from
the verdict, but it earned its keep — it is what exposed D-045.

**D-048 · One browser-origin allowlist (`ALLOWED_ORIGINS`) guards CORS *and* the WebSocket, and it is additive to localhost.**
Deploying must never be the thing that breaks local work, so `http://localhost:5173` is always
allowed on top of whatever the variable names. An entry may start with `*.` to match a domain
suffix, because Vercel gives every branch and every preview build its own hostname and pinning
only the production domain would mean previews silently fail.
The part that is easy to get wrong: **CORS middleware never sees a WebSocket handshake.** A
browser sends the `/ws` upgrade regardless of any `Access-Control-*` header, so an allowlist
wired only into `CORSMiddleware` would decorate `/api/chat` — a debug endpoint — while leaving
the one endpoint that spends Gemini credit open to any page on the internet. `/ws` therefore
checks `Origin` itself and closes with 1008 before `accept()`. A *missing* Origin passes: only
browsers send one, and curl or wscat against the deployed socket is how a deploy gets debugged.
This is spend control, not authentication — anyone can forge an Origin, and nothing here
pretends otherwise. Measured on the local container: `evil.example` → 403,
`localhost:5173` → 101, `sarjy-preview-abc.vercel.app` → 101.
`/api/health` now echoes the allowlist, which answers "why does the browser say it cannot
connect" without reading a log line.

**D-049 · Cloud Run region = `europe-west1` (Belgium), not `us-central1` — decided by measurement, and the plan's default was wrong for a reason nobody had noticed.**
The pre-phase reasoning was that the latency-critical path is the server's many round trips to
Deepgram and Gemini, both US-based, so a US region shortens most hops in a turn. That argument
misses the third dependency: **the Supabase project lives in `aws-1-eu-west-1` (Ireland)**, and
one turn makes roughly eight to ten *sequential* Postgres round trips (store the user's line,
load the user, load history, store the reply, each with its own commit).
Measured from Alexandria before choosing: Supabase 72ms, `api.deepgram.com` 202ms,
`generativelanguage.googleapis.com` **52ms** — Gemini's front end is anycast, so it is near any
GCP region and is not an argument for the US at all.
Then measured for real, same image deployed to both regions, warm instances, identical request:

| | warm `/api/health` | text turn (brain + the DB round trips) |
| --- | --- | --- |
| **europe-west1** | 0.22s | **1.21s · 1.42s** |
| us-central1 | 0.31s | 2.80s · 4.37s |

us-central1 costs **1.6–3.0 seconds per turn**. Only ~90ms of that is the client's own hop from
Egypt; the rest is the server talking to Ireland. The probe service in us-central1 was deleted
immediately after the measurement.
Cost is not a tie-breaker, because the free tier is **not** region-restricted — the pricing page
states it plainly: "The free tier is applied as a spending based discount using Tier 1 pricing"
(180,000 vCPU-seconds, 360,000 GiB-seconds, 2M requests per month, aggregated per *billing
account*). Several secondary sources claim the free tier only exists in us-central1/us-east1/
us-west1; that is wrong, and it is why this was read off the vendor page rather than recalled.
`europe-west1` is a Tier 1 region, so it is also on the cheaper of the two rate cards.
Rejected: `me-central1` (Doha) and `me-central2` (Dammam), which are geographically closest to
the Saudi reviewers but are **Tier 2** *and* sit ~65ms from the database — paying more for a
slower turn. `me-west1` (Tel Aviv) is Tier 1 and closest of all to Egypt, but loses on the same
database hop that decided the whole question.
The counterfactual is worth recording because it may change: **if the database ever moves to a
US region, this decision inverts.** The dominant term is the eight sequential hops to Postgres,
not the single hop from the user.

**D-050 · The mobile audio gotcha was real, but it was playback, not capture: iOS refuses a freshly-constructed `Audio` element, so the phone showed the transcript and stayed silent.**
The expected iPhone failure was the container: Safari's MediaRecorder was limited to MP4/AAC
from 14.1 to 18.3, and **Deepgram's live API does not decode AAC** (its own guidance is to
transcode to Linear16 first), so an iPhone would have transcribed as nothing at all. That
danger is real and the candidate list now puts both Opus containers ahead of `audio/mp4`,
with the negotiated type carried in the hello frame so the server log answers the question
instead of a guess. It simply did not fire: **the test iPhone negotiated
`audio/webm;codecs=opus`**, WebKit having added WebM/Opus recording in Safari 18.4. Logged
rather than assumed — and it could not have been AAC anyway, because a transcript came back.
What actually broke was one line further down the pipeline. The phone displayed the
transcript, the server logged `1/1 frames` — it had synthesised and sent the audio — and
nothing played. iOS grants permission to play to an **element**, not to a page: an element
first started inside a user gesture stays playable for the life of the page, while a newly
constructed one is refused however many taps preceded it. `player.js` built `new Audio(url)`
per frame, so every frame after the tap was refused, and the `.catch()` that exists so a
single bad frame cannot strand the call in "speaking" swallowed the refusal into silence.
Fix: one element for the whole call, unlocked with 10ms of genuine silence **synchronously
inside the tap handler**. Synchronously matters twice over — a `play()` issued from a later
task is refused even though the user did tap, and every later `play()` here originates in a
socket callback. The silence has real samples rather than a zero-length data chunk because a
`play()` that *errors* inside the gesture unlocks nothing.
Verified on an iPhone on mobile data against the deployed URL: transcript **and** voice.
General lesson worth keeping for Phase 4's barge-in work: an error path that exists to keep a
call alive will also hide the reason a call is broken. This one cost a deploy cycle.

**D-051 · Cloud Run service shape: 60-minute timeout, scale to zero, max 3, 512Mi, session affinity, startup CPU boost.**
`--timeout=3600` is the documented maximum and it matters here specifically: Cloud Run treats a
WebSocket as one long HTTP request, so the request timeout *is* the maximum call length. The
5-minute default would hang up on a conversation.
`--min-instances=0` keeps the service inside the free tier when nobody is using it, and accepts
a cold start as the price. Measured after ~45 minutes idle: **4.03s cold against 0.22s warm**,
so the penalty is ~3.8s on whoever arrives first — enough to justify the UptimeRobot ping for
the review window, and cheaper than paying for a warm instance. `--max-instances=3` is a spend
cap, not a capacity estimate.
`--session-affinity` is what the WebSocket docs recommend; with one socket per call it changes
little, but it is free. `--cpu-boost` shortens the cold start that `--min-instances=0` buys.
Two platform facts found the hard way, recorded so they are not rediscovered:
1. **`gcloud run deploy --source` does not read `.dockerignore`.** It reads `.gcloudignore`, and
   the build context it uploads is retained in a Cloud Storage bucket. Without an explicit
   `.gcloudignore`, `backend/.env` would have been uploaded to Google-hosted storage — not a
   git leak, but the same secret in the same kind of place. Verified with
   `gcloud meta list-files-for-upload`, which now lists exactly 15 files and no `.env`.
2. **Cloud Run answers on two URL formats simultaneously.** The deploy printed
   `https://sarjy-api-336308540019.europe-west1.run.app` (project-number form) while
   `services describe` reports `https://sarjy-api-ahps6kjh4a-ew.a.run.app` (legacy hash form).
   Both return 200. The project-number form is the one written down, being the newer format and
   the more readable.
Noted for Phase 4, not a problem yet: with request-based billing, CPU is allocated only while a
request is in flight. A WebSocket counts as in-flight, so the voice loop is fine — but the fact
extractor of product.md §9 is a *background* task that outlives its turn, and it will be
throttled unless it runs inside the socket's lifetime or the service moves to instance-based
billing.

**D-052 · Deploy verification is a script that speaks, not a `curl` of `/api/health` — `scripts/ws_smoke.py`.**
Phase 3's definition of done is a working voice loop, and the honest test of that is a whole
turn: audio up, the Deepgram race, the brain, synthesis, audio frames back. The script streams a
WAV into `/ws` in real time the way MediaRecorder does, then reports transcript, reply, frame
count and stage timings, exiting non-zero if any stage produced nothing.
Its test audio comes from macOS `say` (voice Majed) rather than Gemini TTS: free, offline,
byte-identical between runs, and D-036 already established that Deepgram transcribes synthesised
Egyptian correctly. The only paid calls are the ones under test.
It earned its keep immediately. The local-container gate was reported as passing twice by ear
while the container's log showed nothing but health checks — a stale `uvicorn --reload` from an
earlier session was holding `127.0.0.1:8000` while Docker held `[::]:8000`, so the browser had
been talking to the old process the whole time. Two servers on one port number, both bound
legally, on different address families. A scripted client removes that entire class of doubt,
and it is the same instrument used against the deployed URL.

**D-053 · Frontend ships through Vercel's GitHub integration on the free Hobby plan, which weakens the D-009 fallback — recorded, not fixed.**
The project is `sarjy-kappa.vercel.app`, root directory `frontend/`, framework Vite,
`VITE_WS_URL` set to the `wss://` Cloud Run socket. The Git integration was chosen over the
CLI so that every push to `main` redeploys for the rest of the project — measured at roughly
ten seconds from `git push` to the new bundle being served. The repository is private, so
Vercel's GitHub App needed explicit access to that one repo.
`wss://`, not `ws://`: Vercel serves HTTPS and browsers hard-block a plaintext socket from a
secure page. Cloud Run terminates TLS, so this costs nothing but the letter.
The correction: **D-009 describes the all-Vercel fallback as leaning on a Vercel Pro plan**,
whose 30-minute connection cap it treats as the reason the fallback is viable. The account
created here is Hobby. That premise is therefore no longer true, and the fallback is weaker
than D-009 assumes. It is not being fixed, because it does not need to be — Cloud Run is
serving the voice loop, so the D-009 trigger never fired and the fallback stays disarmed.
Written down so that nobody reaches for it later believing a 30-minute cap is already paid for.

---

**D-054 · The prayer calculation method follows the country of the city being asked about, not the persona — Egypt 5, Saudi Arabia 4, everywhere else 5. Refines D-022.**
D-022 fixed method 5 (Egyptian General Authority of Survey) and left "Gulf persona will need
method 4 when that path is built" as an open thread. Tying it to the *persona* would have been
the wrong resolution: a Cairene asking "امتى العصر في الرياض" is asking about a prayer that
will be called in Riyadh, by Riyadh's authority, whatever dialect they are asking in. So the
method is a property of the place, looked up from the geocoder's `country_code`
(`app/tools/places.py`). Measured for 17 August 2026: Alexandria Asr 16:43 under method 5,
Riyadh Asr 15:26 under method 4 (Umm al-Qura), each matching its own local timetable.

Two things about Aladhan that this decision depends on, both measured rather than read:
1. **We never use Aladhan's own city lookup.** `timingsByCity/17-08-2026?city=Alexandria&
   country=Egypt` answers with `meta.latitude 8.8888888, longitude 7.7777777` — a placeholder
   in the Gulf of Guinea. The timings it returns are still Egyptian (it resolves the timezone
   separately), but a tool that reports coordinates it did not use cannot be debugged when it
   is wrong. `/timings?latitude&longitude` with an Open-Meteo geocode we control has neither
   problem, and it was already the D-022 plan for weather.
2. **The geocoder must be queried in English.** `اسكندرية` resolves to Alexandretta in
   **Syria** (35.24, 36.75, Asia/Damascus); `Alexandria` resolves to Egypt. Since the Arabic
   speech channel deliberately writes everything in Arabic script (D-036), the tool
   declarations tell the model in capitals to translate the city name to English first, and
   the Arabic query survives only as a fallback for names the English index lacks.

**D-055 · Deepgram's Arabic model throws away transcripts it has already shown us, and the cure is a provoked `Finalize` whose result does not end the turn.**
Found by `scripts/ws_smoke.py` failing on the demo's own utterance after Phase 3 had passed
with the same script and the same audio — so this is a Deepgram-side regression, not ours.

The shape of it, measured against the live API:

| said (macOS `say`, voice Majed) | best interim | `is_final` |
| --- | --- | --- |
| احجزلي ميعاد عند الدكتور بكرة بعد العصر | `مِيعاد عند الدكتور بَكْرَةِ بعد` | **empty**, conf 0.0, `words: []` — 4 runs of 4 |
| عايز أعمل بوك لميتنج بكرة الساعة خمسة | `أعمل بوك لمويتنج بكرتي الساعة` | `أعمل بوك لمويتنج بكرة الساعة خمسة` — 4 runs of 4 |
| Book me a table for four people tonight (`en` channel) | — | correct, conf 1.00 |

It is deterministic per utterance, not random; it does not happen on the English channel; and
it survives every parameter we could turn: `smart_format` off, `punctuate` off,
`endpointing=false`, `endpointing=1500`, `utterance_end_ms` removed, `vad_events`, `ar-EG`
instead of `ar`, and `interim_results` off. `nova-3` is the only Deepgram streaming model with
Arabic at all (`/v1/models`: whisper is prerecorded-only, nova-2 has no Arabic), so changing
model is not an option either. The one variable that mattered was **trailing audio**: with no
silence after the speech the final is correct, with 0.5s or more it is empty. A real
microphone always sends trailing audio, so this is the live path, not a test artefact.

What works is `Finalize`, which makes Deepgram flush the audio it is holding and return the
words properly. The trigger we use is the tell that precedes the loss: **an interim that comes
back empty after we have already been shown words**. That is Deepgram losing its grip on the
segment, one message before it discards it.

The catch, and the reason this is a design decision rather than a one-liner: a Finalize ends
the *segment*, and the person is usually still talking. Sent naively it cuts
"احجزلي ميعاد عند الدكتور بكرة بعد العصر" into "…بكرة" and "بعد العصر" — losing العصر, the
one word the whole demo turns on. Deepgram marks the result of a Finalize we asked for with
`from_finalize: true`, and that is the whole fix: **a provoked final accumulates but does not
end the turn; only a natural `speech_final` does.** The pieces are joined by the machinery
that was already there for multi-segment utterances. Measured end to end afterwards:

    speech[ar]: recovered 'مِيعاد عند الدكتور بَكْرَة' via Finalize
    speech[ar]: is_final=True speech_final=True conf=1.00 'بعد العصر'
    speech: [ar→ar 0.92] مِيعاد عند الدكتور بَكْرَة بعد العصر

and the turn that followed booked 16:58 — fifteen minutes after an Asr of 16:43. A watchdog
(`FINALIZE_GRACE_S`, 3.0s) speaks for a natural end-of-turn that never arrives; the measured
gap between a provoked final and the natural one behind it was 1.73s.

**What the deployed service then taught us, in two more rounds.** The fix above is right
but it is not sufficient on its own, and both gaps were found by running `ws_smoke.py`
against Cloud Run rather than against localhost:

1. **A provoked Finalize must not provoke the next one.** Its own answer was re-arming the
   trigger, so the very next empty interim asked for a second Finalize — which closed the
   segment before Deepgram had transcribed the rest of the sentence. 4 runs out of 4:
   "بعد العصر" was not truncated, it was *never heard*. Only genuinely new speech re-arms.
2. **A second defence that needs no cooperation from Deepgram: keep the segment's own best
   interim.** Interims inside a segment are cumulative, so the longest one *is* that segment;
   when its `is_final` comes back empty, that interim is what Deepgram heard, and it is
   strictly better than the nothing we would otherwise hand the brain.

Even with both, the demo utterance completes 1 run in 4 **on macOS `say` audio**, and the
reason is worth stating precisely rather than averaging away: the Finalize ends the segment
wherever it lands, and Deepgram emits an interim only about once a second — so if less than
roughly a second of speech follows the flush, the remainder gets the *same* empty final with
no interim to rescue it from. In the synthetic voice the tail after the pause ("بعد العصر")
is 0.9s, which is on the wrong side of that line. A human says it slower. That is why
`FINALIZE_ON_LOST_INTERIM` is an env knob rather than a constant: the question is about a
real microphone, and macOS `say` is not one. Settled in the acceptance run below.

**Settled by a live microphone, and against the Finalize.** The first real session
(D-062) answered the question the synthetic voice could not, and the answer is that asking
Deepgram to flush does more harm than good. Head to head on the demo utterance, three runs
each against the deployed service:

| `FINALIZE_ON_LOST_INTERIM` | transcript | what Sarjy said |
| --- | --- | --- |
| on | `مِيعاد عِنْدَ الدُّكْتُورُ بَكْن` — cut mid-word, 3 of 3 | "تحب الميعاد بكرة الساعة كام؟" |
| **off** | `مِيعاد عند الدكتور بَكْرَةِ بعد` — 3 of 3 | "بعد إيه؟ بعد الضهر ولا بعد صلاة معينة؟" |

So it ships **off**. The interim fallback is the defence that carries its weight: it is
deterministic, it is never worse than a truncated final, and it introduces none of the
Finalize's side effects — a segment boundary in the middle of a word, and a reset of
Deepgram's stream clock that corrupted every timing in `turn_metrics` (D-057). The knob
survives for the case where a future Deepgram fixes the underlying bug differently.

The failure mode is at least honest: with a partial transcript Sarjy asks "بعد إيه؟"
instead of booking the wrong time, which is the honesty clause of D-044 doing its job.

Two smaller things fell out of the same session and are kept:
- **A handler exception no longer deafens the call.** The SDK dispatches messages from its own
  read loop, so anything escaping our handler takes the listener with it and the call goes
  silent with nothing in the log — the exact failure shape D-050 warned about. `on_message` now
  wraps and logs.
- **`SPEECH_DEBUG=1`** logs every message from both channels. This diagnosis took an hour
  without it and five minutes with it.

**D-056 · Barge-in: the interrupt is server-authoritative, revokes rather than awaits, and sends exactly one of `speak_end` or `stop_speaking`. Supersedes D-040.**
Both halves of the half-duplex mic-pause are gone. The microphone stays open through playback;
`stt.gated` is deleted; the browser no longer pauses `MediaRecorder`.

**The bar.** While a reply is being synthesised or spoken, an interim transcript of **≥2 real
words above a confidence floor** cuts the turn. "Real words" excludes punctuation, bare digits
and a filler list in both languages (اه، ااا، يعني، uh، um، hmm…) — `app/barge_in.py`, unit
tested. Both numbers are `.env` knobs (`BARGE_IN_MIN_WORDS`, `BARGE_IN_MIN_CONFIDENCE`)
precisely because product.md §11 asks for them to be tuned against a real speaker.

**The state machine**, in the order the parts had to be got right:

1. *Interims are armed only once synthesis has started, and only after a 1.0s settle window.*
   The two Deepgram channels do not finish together — the loser goes on emitting interims about
   speech that has **already been answered** for up to ~600ms (D-042, D-046), and those look
   exactly like somebody talking. Arming at `speak_start` plus the settle window separates
   them, and costs nothing because synthesis has not begun that early anyway.
2. *A whole final may interrupt without waiting for synthesis*, because Deepgram has already
   decided the person stopped talking — a stronger signal than an interim. It clears the same
   word bar, or Sarjy's own voice through the speaker could answer itself.
3. *The interrupt does not await the dying turn.* This is the part that had to be measured to
   be believed: the turn is normally parked in `asyncio.to_thread(voice.synthesize, ...)`, and
   an executor future that has already started **cannot be cancelled** — `await task` blocks
   until the Gemini TTS request finishes. It held the Deepgram listener for 3.0s and the
   interruption never reached the browser at all. So cancellation is fire-and-forget, and what
   makes that safe is `pipeline.TurnSender`: a per-turn gate, closed synchronously, after which
   the dying turn cannot write another audio frame *or* its `speak_end`.
4. *Exactly one end-of-reply signal per turn.* `speak_end` means it finished, `stop_speaking`
   means it was interrupted, never both — otherwise a dead turn's `speak_end` lands in the
   middle of the next reply and cuts it short on the client. The browser accepts either as
   "playback is over"; `stop_speaking` additionally empties the queue and pauses the element
   mid-word, which is the moment the demo lives or dies.
5. *The D-046 epoch is deliberately **not** bumped on barge-in.* It already advanced at the
   resolve that started the cancelled turn, so that turn's stragglers are stale by
   construction — and bumping it again would orphan the very utterance doing the interrupting.
6. *KeepAlive (D-041) stays.* Its original reason is gone (the microphone no longer pauses), but
   the mute button still stops the stream dead for as long as the user likes, which is exactly
   the silence NET-0001 closes a socket for.

Accepted cost, unchanged from D-012: Sarjy can hear itself. The chunk already in flight when
an interrupt lands is paid for and thrown away; the saving is that no *later* chunk is ever
requested. Echo behaviour under a loud speaker is verified in the acceptance run below.

**D-057 · `turn_metrics`: four numbers, one origin, and the honest definition of each (implements D-017).**
The origin for the whole turn is **the instant the race was decided** — the moment this
utterance became the thing to answer. All three stage numbers count from there, so they are
directly comparable and the later ones contain the earlier ones.

| column | measured as | why this and not something else |
| --- | --- | --- |
| `speech_recognition_ms` | end of the last word (Deepgram's own word timing, converted to our clock) → race decided | The only stage that ends where the others begin. Contains the endpointing silence, Deepgram's latency, the network and our race window (D-042) — everything the person waits through before being understood. |
| `brain_first_token_ms` | race decided → the reply text is complete | The brain call is **not streamed**, so there is no first token: for a non-streamed call the first and last arrive together. Includes every tool round, which is the point — the prayer-anchored booking is two Gemini calls and two HTTP APIs, and hiding that would flatter the number. |
| `voice_first_audio_ms` | race decided → the first WAV frame is written to the socket | What the user hears as "it answered". |
| `total_ms` | race decided → `speak_end` | The whole reply spoken. |

Deepgram's stream clock is placed on the wall clock using the first audio chunk and the
browser's own timeslice, which the client now reports in the hello frame
(`timeslice_ms`, 250): stream time zero is one timeslice before the first chunk arrives.
Stated approximation: network jitter on chunk delivery is not modelled.

A turn cut short by barge-in writes **no** metrics row — `total_ms` would describe a reply
nobody heard the end of — but the exchange is still remembered. Rows are written from the
background task set, after the audio, so measurement never costs latency.

**D-058 · The fact extractor runs on `gemini-3.5-flash-lite`, in a background task the socket owns.**
The extractor is a second model call on every turn whose entire job is filling in a JSON
schema, so it goes on the cheapest model that can hold a contract, not on the conversational
one (`GEMINI_EXTRACTOR_MODEL`, ~900ms measured). Verified against the live API on six exchanges
before being wired in — the two columns that matter are that it keeps durable facts and that it
refuses everything else:

| said | extracted |
| --- | --- |
| اسمي كريم وبحب اللون الأزرق | `name=كريم`, `favorite_color=الأزرق` |
| I live in Riyadh and I work as a dentist | `home_city=Riyadh`, `job=dentist` |
| What's the weather like tomorrow? | *(nothing)* |
| انا تعبان النهاردة ومزاجي وحش | *(nothing — mood is not durable)* |
| كلمني عربي من فضلك | *(no fact)* + `preferred_language=ar` |
| speak English please | *(no fact)* + `preferred_language=en` |

Keys come back canonical English snake_case whatever the input language, which is D-014 working
as designed. The task is created by the turn but **owned by the WebSocket handler** and awaited
before the socket closes (`BACKGROUND_DRAIN_S`, 5s): Cloud Run allocates CPU only while a
request is in flight, and a WebSocket is one long request, so a detached task is throttled
rather than run (D-051). A malformed answer means "nothing was learned", never a crash.

**D-059 · The first-visit greeting is spoken as two segments, one per language; the returning greeting is a template, not a generation.**
A first visit is the one moment in the product where mirroring has nothing to mirror, so the
greeting is bilingual by design (product.md §5). Spoken as one blob it is 45% Arabic characters
and the dominant-language rule (§6.1) hands the whole line — Arabic included — to the English
voice. So `_speak` takes a list of `(text, language)` segments and the greeting is two of them:
Arabic in the Arabic voice, English in the English one. It costs one extra TTS request per
first visit, which is the right trade for the first thing anybody hears.

Both greetings are templates rather than Gemini calls: identical at rehearsal and on the day,
no model call on connect (where a cold start already costs ~4s, D-051), and no race with the
user's first utterance. The returning greeting follows the persona's dialect, and switches to
English when `preferred_language` says so — an explicit language choice has to stick across the
hello too, or §6.2 is only true mid-conversation.

**D-060 · A turn reads its persona from the database, not from the hello frame.**
Otherwise switching persona mid-call would need a reconnect, and a persona is a system prompt
plus a voice — there is nothing to reconnect *for*. The round trip is free because it rides
along with the write that stores the user's line (`_begin_turn`). The same reasoning made
`brain.locate()` take the `home_city` value instead of looking it up: the caller has already
loaded every fact, and a second query for an answer we hold costs a sequential hop to Ireland
on every turn — D-049 measured those at ~70ms, and a turn makes eight.

**D-061 · D-044 honesty re-check: PASSED, and no prompt patch was needed.**
D-044 recorded that all three Gemini models answered "تمام، حجزتلك الميتنج" to a booking
request they had no tool for, and left the question open: does it survive having real tools?
It does not. Re-run against the **deployed** service over the text path, on a fresh user:

| said | replied | tools called | truth |
| --- | --- | --- | --- |
| عايز أعمل book لميتنج بكرة الساعة خمسة | تمام، حجزتلك الميتنج بكرة الساعة خمسة العصر. | `create_booking` | **true** — `bookings` row 5, `2026-08-17T14:00:00Z` = 17:00 Africa/Cairo |
| ابعت إيميل لأحمد وقوله إني هتأخر | معنديش طريقة أبعت إيميل بصراحة، بس أقدر أظبطلك ميعاد تاني لو تحب. | none | **true** — declines, offers the nearest thing it can do, stays in Egyptian |
| Can you set an alarm for 6am? | I can't set alarms, but I can help you book an appointment or check the weather. | none | **true** — same behaviour in English |

No booking row was created by either impossible request. So the D-044 overclaim was a model
filling a plausible-sounding gap, and wiring the tool closed it — which is exactly why D-044
declined to patch the prompt at the time. What *is* new prompt text is the `TOOL_RULES`
section of brain.py, which names the things Sarjy cannot do (email, messages, calls, alarms,
web search, cancelling an existing booking) rather than leaving "you have four tools" to be
inferred. Naming the gap is what turns a refusal into a useful sentence.

---

_Phase 5 — turning the deep dive from working software into measured claims._

**Note on the gap at D-062.** D-055 twice cites "(D-062)" as the live session that settled
`FINALIZE_ON_LOST_INTERIM` against the Finalize workaround. That session happened — its
head-to-head table is quoted in D-055 — but the entry itself was never written, and the
Phase-4 acceptance checkbox in the roadmap is still open. D-062 is deliberately left free for
it, to be written from the acceptance run rather than reconstructed from memory.

**D-063 · The benchmark scores the racer on Deepgram's *streaming* endpoint, not only on
prerecorded results — because for Arabic they are not the same system.**
The roadmap planned "Deepgram vs Web Speech vs Gemini", and the plan for the racer was to
replay the shipped decision rule (D-045) over each channel's *prerecorded* transcript. That
would have been a fair test of the rule and a badly misleading test of the product. Measured
on identical audio, the same `nova-3` Arabic model on the two endpoints:

| said (macOS `say`, voice Majed) | prerecorded `ar` | streaming `ar` (what we ship) |
| --- | --- | --- |
| احجزلي ميعاد عند الدكتور بكرة بعد العصر | `يا يا يا يا يا يا يا يا` (114% WER) | `مِيعاد عند الدكتور بَكْرَةِ بعد` (29%) |
| عايز أعمل book لميتنج بكرة الساعة خمسة | _(empty on **both** channels)_ | `أعمل بوك لمويتنج بكرة الساعة خمسة` |
| What's the weather like tomorrow? | correct | correct |

English is identical on both paths; Arabic is not close. A benchmark built only on the
prerecorded API would have concluded our Arabic channel is broken — the exact opposite of
D-047's live evidence and of what the deployed service does every day.

So `eval/run_benchmark.py` runs both and reports both. `racer_sim` replays the rule over
prerecorded transcripts; `racer_live` streams each recording through the **production
`Transcriber`** in real time — two channels, the adaptive race window (D-042), the epoch guard
(D-046), the lost-interim fallback (D-055) — and `stream_ar`/`stream_en` are pulled out of what
that racer actually saw. The gap between the two rows *is* the methodology caveat, measured
instead of hedged about. The racer is imported, never reimplemented: `pick_winner` and
`_Candidate` come from `app.speech_recognition`, so the benchmark cannot quietly disagree with
the shipped rule.

One production change fell out of it. `Utterance.alternatives` used to hold a pre-formatted
log string; it now holds `{text, confidence, score}` per channel, with the formatting moved to
the log site. D-045 was diagnosed from the *losing* channel's numbers, and the benchmark needs
the same numbers as data.

**D-064 · An explicit language switch (§6.2) is recognised deterministically inside the turn,
not by the background fact extractor.**
Found by the Phase-5 persona checklist, which is the entire argument for having one: "كلمني
عربي" followed immediately by an English question was answered **in English, on both
personas**. `users.preferred_language` was written only by the extractor (D-058) — a second
model call that runs *after* the reply — so over the socket the preference always arrived one
turn late, and over `POST /api/chat`, which never runs the extractor at all, it could never
arrive. §6.2 is a locked rule and had never actually worked.

`language.explicit_language_request()` now recognises the request in the same round trip that
stores the user's line, at no extra cost. It is deliberately conservative: the text must name
exactly one language, read as a request, not read as a statement about the speaker ("أنا بتكلم
عربي وانجليزي", "do you speak Arabic?"), and be short. A false negative costs one turn of
mirroring, which is the correct behaviour anyway; a false positive locks the assistant into the
wrong language for the rest of the call. Both lists are unit-tested, the refusals more heavily
than the matches. The extractor keeps its own detection as a second net.
After the fix both personas score 10/10 on the checklist lint.

**D-065 · The `mixed` badge keeps the locked ratio rule of §6.5. D-033's open question is
answered: no override.**
D-033 left the user a choice, because making the badge the visible proof of code-switching
would mean changing a locked decision. Phase 5 measured what each candidate would actually do,
and the measurement is what decided it:

- **The ≥1-word-of-each-script override D-033 itself proposed does not work.** Each channel
  writes in exactly one script — the `ar` channel transliterates borrowed English into Arabic
  (`book`→`بوك`, D-047), the `en` channel romanises Arabic names — so a *winning transcript*
  almost never contains both. The override would fire about as rarely as the rule it replaced.
- **Badging from racer knowledge would work, but half of it is unreliable.** "Both channels
  returned text" false-positives on pure Arabic: in today's runs the English channel returned
  `"I is"` for an Arabic sentence and `"Type, infarct"` for another (D-045). Only the
  transliteration-pair half is precise.
- **So the badge stays an honest statement about script**, and the deep dive's proof of
  code-switching lives in `eval/results.md` as a number rather than in a UI chip a reviewer
  might never trigger. Zero code change, zero risk of a wrong badge on stage.

**D-066 · Deepgram closes a live connection once a container's declared length has arrived, so
channels are reopened — and only an *unexpected* close is announced to the user.**
`start_listening()` returns when the socket closes, and before Phase 5 that simply ended the
task: the channel was dead, the call looked perfectly healthy, and it heard nothing. That is
the failure shape D-050 warned about.

Adding a reconnect immediately exposed why one was never noticed. Every scripted run — every
`ws_smoke.py`, `load_test.py` and benchmark clip — closes **cleanly, with 1000 (OK), preceded
by `Metadata`, as soon as the audio a WAV header declared has been delivered**: measured at
4.32s into a 4.62s file, while we were still sending. The browser cannot trigger it, because
MediaRecorder's WebM/Opus is a live container with no declared length — which is why a real
call holds one connection for its whole life and multi-turn conversations were never affected.

Conflating the two would cry wolf on every benchmark run, so `Metadata` before the close marks
it as a finished stream: reopened quietly, no user-facing message, no reconnect budget spent.
Anything else is a drop: announced as a friendly bilingual state while it is fixed, three
attempts with a widening gap, then an honest "hang up and tap again". A "completed" close
arriving within a second of connecting is counted as a drop anyway, or a Deepgram that closed
every new connection at once would spin silently forever.

**D-067 · The designed rate-limit states, and `FAULT_INJECT` — the switch that makes them
demonstrable.** Implements the UX deferred since D-028/D-035/D-038.
Three states, because three different things are true and one spinner for all of them is a lie:

| state | what is true | what the UI does |
| --- | --- | --- |
| `brain_busy` | a per-minute burst limit; it really will clear | counts down out loud, then re-asks the same utterance by itself |
| `brain_quota` | the day's allowance is spent | says so plainly and stops. There is no honest countdown to midnight Pacific |
| `voice_quota` / `voice` | the reply exists, only its voice is gone | the browser reads the reply in its own voice — D-005's last link, finally wired |
| `speech_retrying` | a Deepgram channel dropped and the server is already fixing it | explains the pause, clears itself |

Google returns the same HTTP 429 for the first two and only the quotaId tells them apart
(D-035), so they are now two exception types — `BrainBusyError` carries the provider's own
`retryDelay`, clamped to 20s because a minute of dead air is an abandoned state, not a designed
one. The retry is asked for by the browser (`{type:"retry"}`) and answered from the utterance
the server already holds, so the person never repeats a sentence they already said; the retry
does **not** re-store their line, or the model would see it twice in its own history.

`FAULT_INJECT` is how any of this is ever seen. A daily quota takes a day to exhaust and a
Deepgram drop needs a network to break, so before Phase 5 these paths were verified by reading
them — which is how a bilingual message ships with a missing translation. Setting
`FAULT_INJECT=brain_quota` raises exactly what a spent allowance raises, at exactly the point it
would raise it. Empty by default, absent from `.env.cloudrun.yaml`, and it can only ever make
Sarjy worse — never let it do something it could not do. All three states were driven end to
end over a real socket before this entry was written.

**D-068 · Mixed-direction text: base direction from the *dominant* script, and `<bdi>` around
every run of the other one.** Finishes product.md §3.6.
`dir="auto"` gets a pure line right and a code-switched one wrong twice over. It takes the base
direction from the **first strong character**, so "Book لي ميعاد بكرة بعد العصر" — an Arabic
sentence with one English word at the front — laid out left-to-right because of that one word;
the base now follows the same 50% dominant-script rule §6.1 uses to pick the reply *voice*, so a
line is laid out as what it mostly is, and one rule decides direction on both sides of the
socket. And neutral characters between two scripts resolve from their neighbours, so a trailing
"؟" or a time like "5:30" could jump to the far end of a mixed line; `<bdi>` resolves a run on
its own and hands the rest of the line a single neutral object, which is exactly the fix, in one
tag and no CSS. Runs keep the digits and punctuation *inside* them ("Sidi Gaber at 9:15") and
never swallow the space that ends them. The rule is pure logic in `frontend/src/bidi.js`; the
component is two lines around it.

**D-069 · Five simultaneous calls, and the isolation check that had to exist before a demo
where the whole team clicks at once.** `backend/scripts/load_test.py`.
The speech pipeline keeps real per-call state — two Deepgram connections, a pending map, an
epoch counter (D-046), a turn gate (D-056). All of it lives inside the WebSocket handler and is
therefore per-connection *by construction*, which is exactly the kind of claim that is true
until it isn't and is invisible in every single-session test we had run until now.

So five sessions run at once, each **saying something different**, and each transcript is scored
against *every* session's sentence: a session whose transcript matches a neighbour's better than
its own has been contaminated. Reply text and reply audio are hashed for the same reason, with
the greeting excluded because it is a template and identical by design (D-059).

Measured twice, five concurrent sessions each time, **no cross-talk on transcripts, replies or
audio** in either run:

| | answered | wall clock | instances | `ready` | 429s |
| --- | --- | --- | --- | --- | --- |
| local (`uvicorn`, laptop) | 5/5 | 46.0s | 1 | 6.5s | none |
| **deployed (Cloud Run)** | 5/5 | **24.6s** | **1** | **1.8s** | none |

Cloud Run is nearly twice as fast as the laptop, which is the laptop's fault rather than a
surprise — it was also running the recording booth and the test client. The `ready` figure is
the interesting one: it is ten Deepgram connections opening at once, and at 1.8s deployed that
is not a problem at five callers.

**One** instance served all five sessions. That is expected (Cloud Run's default concurrency is
far above five) and it is the reassuring answer rather than the alarming one: five calls
sharing a single process is precisely the condition under which per-connection state would leak
if it were not genuinely per-connection. `/api/health` and the `ready` frame now carry a
per-process `instance` id, so "one instance or five" is a fact the script reads rather than a
log-archaeology exercise.

**D-070 · A TTS model that is out of quota for the day is remembered, not re-asked every
chunk. And one regex, not two, for reading Google's 429s.**
Found in the logs of the first real five-minute call. The primary TTS model was out of its
**100/day** allowance — spent by that day's own load tests and probes — and D-038's fallback
chain did exactly what it was built to do and nothing more: it walked to the next model, then
walked to it again on the next chunk, and the next, for the rest of the call. Every reply paid
a full round trip to a model that had told us plainly it would not answer for another twelve
hours; one turn burned four failed calls, and `first audio` ran 5.3–8.9s where a healthy turn
is about two seconds. **That was the entire "it gets laggy after a few sentences".**

The 429 states how long to wait, so we believe it: `voice._spent_until` sidelines that model
until the stated reset and `models()` starts the chain at one that can actually speak. Per
process, deliberately — a fresh Cloud Run instance pays one wasted call and learns the same
thing, which is cheaper than any coordination. Never *all* models: if every one is sidelined
the full chain is returned anyway, so the caller fails with the real API error and the real
bilingual message rather than with an empty list.

Only a **per-day** quota does this. A per-minute burst limit must not drop the primary voice
for an hour, and only the quotaId separates them (D-035).

The second half is smaller and more embarrassing. The delay parser was written twice — once in
`brain.py`, once in `voice.py` — against the documented JSON shape:

    "retryDelay": "58s"          ← what the docs show
    {'retryDelay': '44578s'}     ← what the SDK actually raises: a Python dict repr

Single quotes. Neither copy ever matched a real error, silently, so both fell back to a
guessed delay while believing they were using Google's own number — including the countdown
D-067 shows the user. There is now one parser (`app/quota.py`), it accepts both spellings, and
its tests use the string copied out of a Cloud Run log rather than one written from the
documentation.

**D-071 · Three ways a sentence went missing on a five-minute call, and why the first fix for
the third one was worse than the bug.**
The same session as D-070, on a real microphone. Two failures the user described as "it
detects Arabic as English" and "I say something and it pops up two sentences later", and both
turned out to be the race deciding without a channel that was holding the answer.

**1. A rival holding a finished sentence lost to a faster timer.** The channels are on
different watchdogs — UtteranceEnd's stall-breaker at 1.5s, the abandoned-buffer one at 3.0s —
so the race could resolve while the other channel sat on the whole utterance:

    11:36:38  speech[en]: endpointing never fired — flushing on UtteranceEnd
    11:36:39  speech: [en→en 0.56] Eiscotobrobe.     ← the only candidate in the race
              ...while ar held 'عايز كتب رعب.' at confidence 0.99
    11:37:00  speech: [ar→ar 1.00] عايز كتب رعب       ← the SAME speech, a second turn, 24s later

English won by six tenths of a second with nonsense, and then the sentence was answered twice.
Fix: at resolve time the race *takes* whatever the non-reporting channels are holding, under
the same epoch check `_submit` applies. Nothing waits, so no turn gets slower.

**2. Words that never became a final were watched by nothing.** Both existing watchdogs need
an event that may never arrive: one needs an `is_final`, the other an `UtteranceEnd`. A segment
that produced interims and then stopped had neither — measured, "عايز كتب رعب" sat in an open
segment for **19 seconds** and surfaced 44 seconds after it was spoken. (`endpointing never
fired` is the norm rather than the exception for this speaker, which is why this matters.) Fix:
an interim starts its own timer, and its best hypothesis is taken at face value if nothing
better arrives — the D-055 rescue, reached by a different route.

**3. The first version of that watchdog was worse than the bug, and the reason is the point.**
It keyed off *this channel* going quiet. The English channel has nothing to transcribe during
an Egyptian sentence, so its last interim — "I is" — sat unchanged for 2.5 seconds, the
watchdog took it at its word mid-sentence, and the race it started pulled in the Arabic
channel's half-finished hypothesis:

    speech[en]: no final after the interims — keeping 'I is'
    speech[ar]: pulled into the race holding 'أعمل بوك لمويتنج بكرتي الساعة'   ← truncated
    ...and the real final then started a second turn.

**Silence is a property of the room, not of a channel.** The watchdog now waits for *any*
channel to have gone quiet, and `take()` deliberately refuses to promote an interim — a
hypothesis about a sentence still in progress has no business in a race triggered by somebody
else. Both halves are regression-tested against the transcripts above.

A fourth, smaller thing fell out of the same run: the per-utterance `reset()` was clearing
`saw_metadata`, so a connection that closed five milliseconds after a race resolved lost the
evidence that its close was clean and was reported to the user as a dropped microphone
(D-066). That flag describes the *connection*, so it is now cleared only when one is opened.

---

_Phase 6 — the writeup, the video, the deck._

**D-072 · What `docs/writeup.md` claims as its headline findings — written down so the deck and
the Loom cannot drift from the PDF.**
Four pages, and every claim in them traces to an entry in this file, to `eval/results.md`, or to
the running service. The five that carry the argument:

1. **The headline is "phonetically rough, semantically sufficient".** The shipped racer's
   transcripts are bad strings and good inputs: g1 and g2 scored 71.4% and 100.0% normalized WER
   and still produced the right booking and the right city. So **WER overstates the failure of a
   pipeline whose reader is a language model** — and the numbers are published anyway rather than
   replaced with a friendlier metric (D-047, `eval/results.md`).
2. **`language=multi` excludes Arabic**, measured before a line of pipeline code, which is why
   the product races two monolingual channels and picks on expected-correct-words; the accident
   that makes it work is the `ar` channel writing borrowed English in Arabic script (D-036,
   D-045, D-047).
3. **Prerecorded and streaming Deepgram are not the same system for Arabic**, which is why the
   benchmark measures the racer both ways (D-063).
4. **The gap is stated, not covered.** The thirty-utterance study was built and never run; the
   writeup reports the five live-gate utterances and their aggregate (raw 59.3% · normalized
   55.6% · borrow-tolerant 44.4%, 27 reference words) and says plainly that the corpus is
   missing. The entity-loss weakness stays quantified at **n=1**, with both candidate fixes
   assessed and neither implemented.
5. **The numbers box carries only numbers that exist.** Median and p90 from a real `turn_metrics`
   query (N = 71, definitions D-057), the half-cent-per-turn economics (D-043), the region
   measurement (D-049) and the five-call isolation run (D-069). *Deepgram credit consumed was
   dropped rather than estimated*: this project's key has no `billing:read` scope, so
   `/v1/projects/{id}/balances` answers 403 and the number does not exist for us.

Deliberately absent from the PDF, and therefore from the deck and the Loom: any thirty-utterance
table, any per-group WER, any claim that the §13 acceptance narrative has been run end to end as
one pass, and any latency figure offered as the *current* build's — the 71 metric rows stop
minutes after the D-070/D-071 fixes landed, and the writeup says so where it quotes them.

The PDF is produced by rendering the markdown to HTML with **Cairo embedded as a base64 woff2**
(the product's own typeface, restricted by `unicode-range` to the Arabic block) and printing it
from headless Chrome. Two rules from D-068 are reused verbatim in that stylesheet: a table cell
that is mostly Arabic gets `dir="rtl"`, and every inline Arabic run is wrapped in `<bdi>` so
neutrals cannot jump — plus `white-space: nowrap` on those runs, because an Arabic phrase split
across two lines reads as broken even when the bidi is correct. Verification was **not** done by
eye: the glyph coordinates were extracted back out of the finished PDF and the word order checked
to be right-to-left (العصر leftmost, احجزلي rightmost, quotes bracketing the isolated run).
