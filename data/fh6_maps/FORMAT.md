# FH6 Community Map Format

JSON files in this directory are loaded as community landmark maps by
`integrations/forza_location.py` (`import_location_map()` / `reload_all_sources()`).
Say **"import location map \<path\>"** to load one, or **"reload location data"**
to re-scan everything in this directory plus your personal map.

## Shape

```json
{
  "source": "your_map_name",
  "game": "fh6",
  "version": "1.0",
  "description": "One-line description of this map",
  "landmarks": [
    {
      "name": "Landmark Name",
      "x": 0.0, "z": 0.0, "y": 0.0,
      "region": "Tokyo City",
      "type": "landmark",
      "tags": ["iconic", "urban"],
      "notes": "Longer context, shown when the player asks 'tell me about X'.",
      "callout": "Short line spoken the first time the player arrives.",
      "radius": 150.0
    }
  ]
}
```

## Fields

| Field | Required | Notes |
|---|---|---|
| `source` | yes | Short identifier for this map, used as the landmark's `source` tag and in the HUD's per-source counts. |
| `game` | no | Currently only `"fh6"` is used anywhere. |
| `landmarks[].name` | yes | Must be unique across all loaded sources -- if it collides with a builtin or another map's landmark, whichever loads first wins lookups by name. |
| `landmarks[].x` / `z` | yes | Forza's engine-space `PositionX`/`PositionZ`, in metres. **Not** real-world lat/lon. |
| `landmarks[].y` | no | Engine-space height; not currently used for distance/recognition (only x/z are). |
| `landmarks[].region` | no | Free text, but keep it consistent across your map (e.g. always `"Hokubu"`, not sometimes `"hokubu"`) since the HUD's region filter groups by exact string match. |
| `landmarks[].type` | no | One of `landmark`/`drift_zone`/`race`/`mountain`/`coastal`/`parking`/`viewpoint`/`region`/`custom`, or invent your own -- the HUD just displays whatever string is here. |
| `landmarks[].tags` | no | Free-form list, not currently filtered on anywhere but kept for future use and shown in notes. |
| `landmarks[].notes` | no | Longer text surfaced by `get_location_callout_info`("tell me about X"). |
| `landmarks[].callout` | no | Short line spoken by the proactive freeroam alert the first time the player arrives. Falls back to a generic "That's {name}." if omitted. Keep it to one short sentence -- this is spoken while driving, not read. |
| `landmarks[].radius` | no | Per-landmark recognition radius override, in metres. Falls back to the global default (150m) if omitted. |

## Coordinate accuracy

There is no way to get precise FH6 engine-space coordinates without
actually driving to the spot in-game. Community maps (including
`fh6_initial_d_locations.json` in this directory, provided as a worked
example) should be treated as approximate until someone corrects them by
driving there and using **"mark this location as X"**, then exporting
their personal map to replace the placeholder entry.

## Sharing your own map

Say **"export my map as \<name\>"** -- this writes your personal
landmarks (the ones you've marked yourself, not builtin or imported
ones) to `<name>.json` in this exact format, ready to share.
