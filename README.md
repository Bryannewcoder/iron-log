# Iron Log

A self-hosted workout log. One page plus a small Python server — no cloud, no account, no dependencies beyond Python 3's standard library. Your data lives in a single JSON file on your own machine.

## Features

- Log workout sessions ("Push Day", "Leg Day") with exercises, weight, sets, and reps
- Notes per session ("Felt strong, slept 7hr…")
- Rest timer
- PR and one-rep-max tracking with per-exercise history
- Custom exercise library — add your own movements
- History search
- Import / export, plus automatic timestamped backups

## How it works

- `index.html` — the whole app, a single page
- `ironlog_server.py` — serves the page on port 8080 and stores everything in `data/workouts.json` (workouts, custom exercise library, one-rep-max history)
- The page syncs with `POST /api/sync`, merging by workout ID so nothing gets lost: on a collision the newest edit wins, and the exercise library and 1RM history merge without dropping entries
- Every sync also writes a timestamped backup to `data/backups/`

## Quick start

```bash
python3 ironlog_server.py
```

Then open `http://<your-pi-ip>:8080` in a browser on your home network.

The server creates the `data/` folder and an empty `workouts.json` on first run. To try it with demo data, copy the sample file in:

```bash
cp sample-workouts.json data/workouts.json
```

## Data format

`data/workouts.json` looks like this:

```json
{
  "version": 1,
  "exported": null,
  "workouts": [
    {
      "id": "sample-1",
      "date": "2026-01-05",
      "name": "Push Day (Sample)",
      "startTime": "17:30",
      "endTime": "18:25",
      "notes": "Sample entry.",
      "lastModified": "2026-01-05T18:25:00",
      "exercises": [
        {
          "id": "ex-1",
          "name": "Example Bench Press",
          "sets": [
            { "weight": 135, "reps": 10, "rest": 90 },
            { "weight": 135, "reps": 8, "rest": 90 }
          ]
        }
      ]
    }
  ],
  "library": [],
  "oneRepMaxes": {}
}
```

## Privacy and security

Iron Log has **no login or authentication** — anyone who can reach the server can read and change your workouts. Keep it on your home network (or behind a VPN / Tailscale). Do not expose port 8080 to the internet.

## License

MIT — see [LICENSE](LICENSE). Use it, fork it, improve it.

## Questions or bugs

Please open an [Issue](../../issues) on this repo.
