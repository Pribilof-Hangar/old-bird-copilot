# Custom sounds

Drop a `.wav` file here named after a callout key, and it plays instead of the voice for that callout.

| File | Callout |
|---|---|
| `1000.wav`, `500.wav`, `400.wav` … `20.wav` | height callouts (one per number in `heights`) |
| `retard.wav` | retard (also used for the repeats) |
| `minimums.wav`, `approaching_minimums.wav` | minimums |
| `speed_low.wav`, `speed_high.wav`, `sink_rate.wav`, `too_low_gear.wav` | warnings |
| `touchdown.wav` | touchdown (if `announce_touchdown = true`) |
| `rollout_80.wav`, `rollout_60.wav` … | rollout callouts |

Notes:

- Use plain PCM `.wav` files. Windows `PlaySound` doesn't play mp3.
- Keep the countdown files (50 … 20) short, well under a second, or they'll fall behind in the flare. Callouts that can't start in time are skipped.
- A wav replaces the whole callout, so `1000.wav` replaces "one thousand, speed …" too.
- To use a different folder, change `sounds_dir` in `config.toml`.
