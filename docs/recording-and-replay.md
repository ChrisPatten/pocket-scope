# Recording & Replay

PocketScope ships with utilities for capturing event streams to disk and replaying them for regression testing, demos, or offline analysis. The main entry points live in `src/pocketscope/tools/record_replay.py`.

## Recording

`JsonlRecorder` subscribes to selected topics, serialises events with MsgPack, and appends them to a JSONL file along with timestamps from the active time source.

```python
from pocketscope.tools.record_replay import JsonlRecorder

recorder = JsonlRecorder(bus, ts, "flight_data.jsonl", topics=["adsb.raw", "gps.position"])
await recorder.start()
...
await recorder.stop()
```

Tips:

- Use narrow topic lists to keep files small and privacy-friendly.
- Store metadata (location, antenna setup, weather) alongside recordings so they are easy to catalogue later.
- Combine with screenshot automation to capture visual context for each scenario.

## Replay

`JsonlReplayer` consumes the recorded file and republishes each event at the appropriate simulated time. When used with `SimTimeSource` it produces deterministic behaviour across runs.

```python
from pocketscope.tools.record_replay import JsonlReplayer
from pocketscope.core.time import SimTimeSource

sim_ts = SimTimeSource()
replayer = JsonlReplayer(bus, sim_ts, "flight_data.jsonl", rate=2.0, loop=False)
await replayer.run()
```

Features:

- Adjustable playback rate (faster or slower than real time).
- Looping support for unattended kiosks or demos.
- Automatic publication of `recording.started` and `recording.finished` events so the UI can annotate the session.

## Workflow Integration

- **Regression Tests** – Pair recordings with golden-frame render tests to lock down behaviour changes.
- **Field Capture** – Record in-flight sessions to reproduce edge cases in the lab.
- **Training & Demos** – Ship curated recordings with the hardware to demonstrate capabilities without live reception.

Because recordings are just JSONL files, they are easy to version control, share, and diff when debugging protocol changes.
