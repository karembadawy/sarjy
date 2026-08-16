# -*- coding: utf-8 -*-
"""The recording booth for the benchmark set — a local page that writes straight into
`eval/recordings/`.

Thirty utterances have to be recorded, named exactly, and matched to a row of `truth.csv`.
Doing that by hand is thirty downloads and thirty renames, and one mis-rename silently scores
the wrong audio against the wrong truth for the rest of the project. So the browser records
and this server files it:

    cd eval && ../backend/venv/bin/python record.py      → http://localhost:8765

The page reads `truth.csv`, shows one prompt at a time with its condition, records on the
spacebar, and POSTs the finished WAV back under the row's own filename. Nothing is ever named
by a human.

Why the browser at all, when a CLI recorder would be fewer moving parts: the benchmark is
supposed to measure what *Sarjy* hears, and Sarjy hears a browser microphone with
`echoCancellation`, `noiseSuppression` and `autoGainControl` on (frontend/src/audio/recorder.js).
Recording through a different capture path would measure a microphone the product never uses.
There is no ffmpeg on this machine either, so the browser is also the only resampler we have.

The page writes 16 kHz mono 16-bit WAV — Deepgram's preferred rate, accepted by Gemini's audio
input, and small enough that thirty utterances are a few megabytes. `eval/recordings/` is
gitignored (golden rule: the recordings are available on request, not in the repository).

The same server hosts the Web Speech harness at /webspeech (see webspeech_harness.html), which
needs a `http://localhost` origin to be allowed a microphone at all.
"""

from __future__ import annotations

import argparse
import csv
import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

EVAL_DIR = Path(__file__).resolve().parent
RECORDINGS = EVAL_DIR / "recordings"
TRUTH = EVAL_DIR / "truth.csv"
WEBSPEECH_RESULTS = EVAL_DIR / "webspeech_results.csv"

# One WAV per utterance is at most a couple of hundred kilobytes at 16 kHz mono; anything
# larger than this is a bug in the page, not a long sentence.
MAX_UPLOAD_BYTES = 8 * 1024 * 1024


def script_rows() -> list[dict]:
    """Every row of truth.csv, with whether its audio has been recorded yet."""
    with TRUTH.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        wav = RECORDINGS / f"{row['filename']}.wav"
        row["recorded"] = wav.exists()
        row["bytes"] = wav.stat().st_size if wav.exists() else 0
    return rows


class Handler(BaseHTTPRequestHandler):
    # The default logs one line per request, which buries the one message that matters.
    def log_message(self, *args) -> None:  # noqa: D102
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # No caching: the page is edited while it is being used.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _send_file(self, path: Path, content_type: str) -> None:
        if not path.exists():
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        self._send(200, path.read_bytes(), content_type)

    # ---- GET ------------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's spelling
        route = urlparse(self.path).path

        if route in ("/", "/index.html"):
            self._send_file(EVAL_DIR / "record.html", "text/html; charset=utf-8")
        elif route == "/webspeech":
            self._send_file(EVAL_DIR / "webspeech_harness.html", "text/html; charset=utf-8")
        elif route == "/api/script":
            self._send_json(200, script_rows())
        elif route.startswith("/recordings/"):
            name = Path(route).name
            if not name.endswith(".wav") or "/" in name.strip("/"):
                self._send(400, b"bad name", "text/plain; charset=utf-8")
                return
            self._send_file(RECORDINGS / name, "audio/wav")
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    # ---- POST -----------------------------------------------------------------------

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_UPLOAD_BYTES:
            self._send_json(400, {"error": f"refusing a {length}-byte body"})
            return
        body = self.rfile.read(length)

        if parsed.path == "/api/save":
            self._save_recording(parse_qs(parsed.query).get("filename", [""])[0], body)
        elif parsed.path == "/api/webspeech":
            self._save_webspeech(body)
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def _save_recording(self, filename: str, body: bytes) -> None:
        # The filename must be one this project already knows about. A name that came from
        # anywhere else is a bug, and this server can write files.
        known = {row["filename"] for row in script_rows()}
        if filename not in known:
            self._send_json(400, {"error": f"unknown utterance {filename!r}"})
            return

        RECORDINGS.mkdir(parents=True, exist_ok=True)
        destination = RECORDINGS / f"{filename}.wav"
        destination.write_bytes(body)
        done = sum(1 for row in script_rows() if row["recorded"])
        total = len(known)
        print(f"  saved {destination.name}  ({len(body) / 1024:.0f} KiB)   {done}/{total} recorded")
        self._send_json(200, {"saved": destination.name, "bytes": len(body),
                              "done": done, "total": total})

    def _save_webspeech(self, body: bytes) -> None:
        """Append one Web Speech observation. Manual protocol — see results.md."""
        try:
            row = json.loads(body)
        except ValueError:
            self._send_json(400, {"error": "not JSON"})
            return

        fields = ["filename", "lang", "heard", "note"]
        new_file = not WEBSPEECH_RESULTS.exists()
        with WEBSPEECH_RESULTS.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            if new_file:
                writer.writeheader()
            writer.writerow({key: row.get(key, "") for key in fields})
        print(f"  web speech: {row.get('filename')} [{row.get('lang')}] {row.get('heard')!r}")
        self._send_json(200, {"ok": True})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true", help="do not open a browser")
    args = parser.parse_args()

    rows = script_rows()
    done = sum(1 for row in rows if row["recorded"])
    url = f"http://localhost:{args.port}/"

    print(f"\n  Sarjy recording booth — {done}/{len(rows)} utterances already recorded")
    print(f"  {url}                (the 30-utterance benchmark set)")
    print(f"  {url}webspeech       (the Web Speech API harness)")
    print("\n  Chrome will ask for the microphone once. Ctrl-C here when you are done.\n")

    if not args.no_open:
        webbrowser.open(url)

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        rows = script_rows()
        done = sum(1 for row in rows if row["recorded"])
        missing = [row["filename"] for row in rows if not row["recorded"]]
        print(f"\n\n  {done}/{len(rows)} recorded.")
        if missing:
            print(f"  still missing: {', '.join(missing)}")
        else:
            print("  the whole set is recorded — run `python run_benchmark.py` next.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
