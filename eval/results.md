# Speech recognition: what Sarjy actually hears

The bilingual deep dive: the method for measuring it, the harness that measures it, and the
measurements that exist so far.

> **Read this first.** The thirty-utterance study described below was **designed and built but
> not run** — the corpus was never recorded, so the eight-system table is empty. What *is*
> measured is a five-utterance live session with recorded ground truth, scored with the same
> rules. Both facts are stated wherever they matter, and nothing here is estimated to cover the
> gap. If you are reading only one section, read **What the numbers say** at the end.

The recordings would not be in the repository in any case (`eval/recordings/` is gitignored —
they are a few megabytes of one person's voice); `eval/truth.csv` lists every utterance, its
group, its condition and exactly what was to be said, and everything is reproducible from those
files with one command.

## The set that was designed

*(Recorded: none of it yet. `run_benchmark.py` scores whatever exists, so a partial corpus —
even three utterances per group — produces a real table in place of the empty one below.)*

| group | count | content | conditions |
| --- | --- | --- | --- |
| `ar` Egyptian Arabic | 10 | weather, a prayer-anchored booking, durable facts, another city | 6 quiet · 2 noisy · 2 fast |
| `en` English | 10 | weather, a table booking, durable facts, a reschedule | 6 quiet · 2 noisy · 2 fast |
| `mixed` code-switched | 10 | the canonical "عايز أعمل book لميتنج", plus **4 tagged `entity`** | 6 quiet · 2 noisy · 2 fast |

The four `entity` utterances are deliberate. D-047 recorded one honest weakness in the shipped
racer — an English sentence carrying Arabic proper nouns loses them ("Dina" → "dinner",
"Maghrib" → "my rib") — from a single live utterance, and said that Phase 5 is where it gets a
number instead of an anecdote. Those four are that number.

*Noisy* means real background noise playing in the room, not added afterwards. *Fast* means
spoken the way you talk to someone you know, not enunciated for a microphone. Recording is done
through `eval/record.py`, which captures with the same `echoCancellation` / `noiseSuppression` /
`autoGainControl` constraints the product's own microphone uses — so it would measure the
capture path Sarjy actually listens through, not a nicer one.

Deliberately **not** substituted while the corpus is missing: synthesised speech. It was tried,
and it measures the wrong thing — Deepgram's prerecorded Arabic model returns `يا يا يا يا` for
the macOS speech synthesiser (see *Why the racer is measured twice*, below). A table built on
that would look rigorous and describe a text-to-speech voice rather than a recogniser.

## Systems under test

*(All eight are implemented and were exercised end to end during development; none has been
run over a recorded corpus.)*

| system | what it is |
| --- | --- |
| Deepgram `ar` alone · prerecorded | one channel, the prerecorded API |
| Deepgram `en` alone · prerecorded | the other channel, same API |
| The racer, simulated on prerecorded | the shipped decision rule (D-045) replayed over those two |
| Deepgram `ar` alone · streaming | what the `ar` channel produced inside a real streaming session |
| Deepgram `en` alone · streaming | likewise |
| **The racer as shipped · streaming** | the production `Transcriber`, both channels, real time |
| Gemini audio | the brain model (`GEMINI_MODEL`), audio in and text out |
| Web Speech API | the browser's own recogniser — manual protocol, see below |

The racer is not reimplemented for the benchmark. `run_benchmark.py` imports `pick_winner` and
`_Candidate` from `app.speech_recognition`, so if the shipped rule ever changes and this table
does not, the mismatch is impossible rather than merely unlikely.

### Why the racer is measured twice

The plan was to simulate the racer over prerecorded transcripts and note, as a caveat, that
streaming and prerecorded model behaviour "can differ slightly". Measured on identical audio,
they do not differ slightly:

| said | prerecorded `ar` | streaming `ar` (shipped) |
| --- | --- | --- |
| احجزلي ميعاد عند الدكتور بكرة بعد العصر | `يا يا يا يا يا يا يا يا` | `مِيعاد عند الدكتور بَكْرَةِ بعد` |
| عايز أعمل book لميتنج بكرة الساعة خمسة | _(empty on **both** channels)_ | `أعمل بوك لمويتنج بكرة الساعة خمسة` |
| What's the weather like tomorrow? | correct | correct |

English is identical on both paths. Arabic is not close. A benchmark built only on the
prerecorded endpoint would have reported that our Arabic channel does not work — the opposite
of what the deployed service does every day, and of the live evidence in D-047. So both are
reported, and the gap between the two racer rows is the methodology caveat, measured instead of
hedged about. `racer_live` streams each recording through the real `Transcriber` in real time,
including the adaptive race window (D-042), the epoch guard (D-046) and the lost-interim
fallback (D-055). Full reasoning in D-063.

### The Web Speech API, and its manual protocol

The browser's recogniser cannot be pointed at a file — it only hears a live microphone. So
`eval/webspeech_harness.html` plays each recording out loud through the speakers and lets
Chrome listen to the room, for a **labelled subset of ten** utterances covering all three
groups. Those rows are marked ¹ in the tables and are not comparable to the others on equal
terms: they have been through a speaker and a room.

Two further things are worth stating rather than hiding:

- **Web Speech has no code-switching mode at all.** It is given exactly one `lang` and hears
  everything as that language. Each clip is therefore run in the language of its group, and
  each *code-switched* clip is run in **both** `ar-EG` and `en-US`, keeping whichever scored
  better. That is oracle selection — a real user could not know in advance which to set — so
  this row is the **most generous possible** reading of the baseline. Being generous to the
  baseline is the safe direction for a benchmark whose author has a stake in the alternative.
- If the subset proves impractical (Chrome cancelling its own speaker output as echo is the
  likely way), it is reported as a gap rather than filled in. An honest gap beats fake rigor.

## Scoring: three passes, and why one number would be a lie

Arabic WER has traps that English WER does not, and each of them is handled in the open, as its
own pass, so that nothing is quietly forgiven. The rules live in `eval/scoring.py` and are unit
tested in both directions — what each must fold together, and what it must refuse to.

**Pass 1 — raw.** No normalization whatsoever. Reported because a normalizer can hide real
errors as easily as it can reveal them, and the reader deserves the unadjusted number next to
the adjusted one.

**Pass 2 — normalized.** Applied identically to truth and hypothesis:

1. Unicode NFKC.
2. Arabic diacritics and the tatweel removed. *Deepgram vocalises some words and not others,
   at random, inside one sentence — "بَكْرَةِ" and "بكرة" are the same word said once.*
3. أ إ آ ٱ → ا · ة → ه · ى → ي · ؤ → و · ئ → ي. *Which hamza carrier a transcriber picks is
   spelling, not hearing.*
4. Arabic-Indic digits → ASCII digits. *٥ and 5 are one digit in two scripts.*
5. Latin lowercased.
6. Apostrophes deleted ("doctor's" is one word); every other punctuation mark becomes a space.
7. Whitespace collapsed.

**Deliberately NOT normalized:** a digit against a spelled-out number. "الساعة 5" where the
truth says "الساعة خمسة" stays an error, because those are two different things for a voice
assistant to say out loud (golden rule 9), and `smart_format` is on in production — if it costs
us, that is a real cost of the configuration we ship.

**Pass 3 — borrow-tolerant.** Pass 2, plus a lookup table (`eval/borrow_pairs.csv`) mapping
Arabic-script renderings of borrowed English words onto the tokens the truth uses for them.
This exists because of a structural fact, not a convenience: **the `ar` channel writes borrowed
English in Arabic script** — `book` → `بوك`, `weather` → `الوذر` — which is exactly what
product.md §6.4 asks the *replies* to do, and which D-047 found is the reason code-switching
survives at all. Scored against a truth written in the script the speaker actually spoke, that
correct behaviour costs one substitution per borrowed word.

The table's membership rule is narrow on purpose, because this is the pass that could be used
to cheat:

- A row exists only where the truth writes the word in **Latin** and a channel wrote the **same
  word** in Arabic script.
- Words the truth itself already writes in Arabic (ميتنج، الأوفيس، الدنتيست) are absent — both
  sides already agree, so there is nothing to forgive.
- **A mis-hearing is never added, however close it looks.** "مارب" for "Maghrib" has lost a
  letter; that is a recognition error and it stays one.
- Every row carries the utterance or decision it came from, in a third column.

**Aggregation.** A group's WER is total errors ÷ total reference words, never the mean of
per-utterance rates — a three-word utterance does not get the same vote as a fifteen-word one.
A system that returned nothing for an utterance is scored as an empty hypothesis rather than
skipped, because silence is a failure and dropping it would reward being silent.

## Reproducing this

```
backend/venv/bin/pip install -r eval/requirements.txt
backend/venv/bin/python eval/record.py                     # record the thirty utterances
backend/venv/bin/python eval/run_benchmark.py --write      # transcribe, score, rewrite below
backend/venv/bin/python -m pytest eval                     # the scoring rules themselves
```

Every raw transcript is cached under `eval/raw/`, so the scoring can be re-run and the borrow
table extended without spending another API call — and so any number below can be traced back
to the exact string that produced it.

## Status: the thirty-utterance study has NOT been run

Stated first, and plainly, because everything below has to be read in that light. The corpus
was never recorded, so the eight-system table has no numbers in it. What exists is the harness
— the recording booth, the eight systems, the three scoring passes, the caching, the tests —
and it is proven end to end on synthesised audio, but a harness is not a measurement.

Nothing here is estimated, extrapolated or synthesised to fill the gap. The project's own
standard is that **an honest gap beats fake rigor**, and this is the gap.

What *is* measured, below, is a smaller thing that was genuinely recorded: five utterances
spoken into a live microphone at the Phase-2 go/no-go gate, whose transcripts and ground truth
were both written down at the time (D-047). Scored with exactly the rules above.

To close the gap: record with `eval/record.py` and run `run_benchmark.py --write`. It scores
whatever exists, so even nine utterances — three per group, about four minutes — replaces this
section with a real table.

<!-- BEGIN GENERATED -->
_Not yet generated — run `backend/venv/bin/python eval/run_benchmark.py --write` once the
recordings exist._
<!-- END GENERATED -->

## What was measured: the live go/no-go session

<!-- BEGIN LIVE GATE -->
_Scored by `eval/score_live_gate.py` from the transcripts recorded in D-047 — 5 utterances, one live microphone, ground truth written down at the time._

| # | said | winning channel | raw | normalized | borrow-tolerant |
| --- | --- | --- | --- | --- | --- |
| `g1` | عايز أعمل book لميتنج بكرة الساعة خمسة | `ar` أيزعمل بوكينج لميتينج بوكرا الساعة خمسة | 71.4% | 71.4% | 57.1% |
| `g2` | ايه ال weather بكرة في اسكندرية | `ar` هو الوذر بوكلا في إسكندرية يكون عملي | 116.7% | 100.0% | 66.7% |
| `g3` | Remind me to call Dina after Maghrib | `en` Remind me to call dinner tomorrow after my rib | 57.1% | 57.1% | 57.1% |
| `g4` | الجو النهارده عامل ايه | `ar` الجو النهارده عامل ايه | 0.0% | 0.0% | 0.0% |
| `g5` | آه يا ريت | `ar` آه يا ريت | 0.0% | 0.0% | 0.0% |

**Aggregate over the 5 utterances** (total errors ÷ total reference words, never the mean of the rates above): raw **59.3%** · normalized **55.6%** · borrow-tolerant **44.4%** (27 reference words).

### The same utterance, both channels — the D-047 weakness class

| channel | transcript | normalized WER | proper nouns |
| --- | --- | --- | --- |
| `ar` | ريميند ميتو كول دينا تمورو أفضل مارب | 100.0% | kept (دينا, مارب) |
| `en` ← won the race | Remind me to call dinner tomorrow after my rib | 57.1% | **lost** (Dina→dinner, Maghrib→my rib) |

<!-- END LIVE GATE -->

**Read these numbers carefully, in both directions.**

They are high — 55.6% normalized, 44.4% borrow-tolerant — and that is not hidden. Two of the
five utterances scored 0%, and the other three scored badly for a reason worth naming: the
Arabic channel writes what it hears **phonetically**, so "عايز أعمل" comes back as "أيزعمل" and
"بكرة" as "بوكرا". Every one of those is a word error, and a WER of 55% is a fair description
of the *string*.

It is not a fair description of the *system*, and the gap between those two statements is the
most interesting thing in this file. The consumer of this transcript is not a human reader — it
is Gemini, which read straight through the transliteration. On g1 the brain parsed a booking
request and answered it; on g2 it answered about the weather in Alexandria tomorrow. That is
what D-016's bar actually asked for ("meaning intact") and it is why the gate passed.

So: **WER systematically overstates the failure of this pipeline**, because it scores spelling
in a system where only meaning has to survive. Reporting the number anyway — rather than
inventing a kinder metric — is the point. The honest summary is that the racer's transcripts
are phonetically rough and semantically sufficient, and the thirty-utterance study is what
would turn that sentence into a distribution instead of five points.

## The D-047 weakness class

One utterance, and it is the one the whole class is named for: **"Remind me to call Dina after
Maghrib"** — an English sentence carrying Arabic proper nouns.

The English channel won the race with 57.1% WER, and lost both proper nouns: *Dina* became
*dinner*, *Maghrib* became *my rib*. The Arabic channel scored **worse** on WER (100%, because
it romanised the English words into Arabic script) and **kept both names** — دينا and مارب.
The better transcript, for this product's purposes, lost the race.

That is exactly the shape D-047 recorded, now with numbers attached, and it is also the clearest
demonstration in the project that **the racer's scoring rule optimises for the wrong thing on
this class of utterance**. `content_score` is confidence × word count (D-045); it has no notion
of whether a proper noun survived.

The two candidate fixes D-047 proposed, assessed against what we have:

1. **Bias the race toward the channel that preserves named entities.** Attractive and unproven.
   With n=1 there is no way to tune a bias without over-fitting to this single sentence, and a
   bias strong enough to flip this case would hand English-language turns to the Arabic channel
   whenever a name appears. **Not implemented** — it needs the four `entity` recordings in
   `truth.csv` before anyone can honestly pick a threshold.
2. **Feed the brain both transcripts and let it choose.** Cheaper to reason about — no tuning,
   no threshold — and it fits the existing prompt, which already tolerates rough input. Costs
   input tokens on every turn and risks the model blending two transcripts into a sentence
   neither channel said. **Recommended for a future phase**, and it is the one I would try
   first, but not on the evidence of one utterance.

**Recommendation: implement neither yet.** Any change to the race is a decision entry that
invalidates the benchmark and requires re-running it, and right now there is no benchmark to
invalidate. Record the corpus first; the four `entity` utterances exist precisely to make this
call answerable with data.

## What the numbers say

For a reviewer reading only this section:

- **The thirty-utterance benchmark was not run.** The harness is complete and tested; the
  corpus was never recorded. Everything below rests on five live utterances, not thirty.
- **On those five, the shipped two-channel racer scored 55.6% normalized WER and 44.4%
  borrow-tolerant.** Two were transcribed perfectly; the rest were phonetically rough.
- **Meaning survived where spelling did not.** Both code-switched utterances produced correct
  downstream behaviour — a booking parsed, a weather question answered about the right city and
  day. WER measures the wrong thing for a pipeline whose reader is a language model, and this
  file reports it anyway rather than substituting a friendlier metric.
- **The one known weakness is real and quantified at n=1**: an English sentence carrying Arabic
  proper nouns loses them (Dina→dinner, Maghrib→my rib), and the channel that kept them lost
  the race. Two candidate fixes are assessed above; neither is implemented, because one
  utterance is not enough to choose between them.
- **The measured finding that changed the design** is elsewhere and is solid: Deepgram's
  prerecorded and streaming endpoints are not the same system for Arabic (D-063). The
  prerecorded API returned `يا يا يا يا يا يا يا يا` for a sentence the streaming model
  transcribes cleanly, and returned nothing at all for the canonical code-switch. A benchmark
  built only on the prerecorded endpoint — which is what the plan called for — would have
  concluded that our Arabic channel does not work.
