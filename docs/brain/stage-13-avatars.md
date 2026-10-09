# Stage 13: Avatars

**Status:** not started
**Depends on:** stage 11 (uses the image client)
**Spec:** [CONTRACT.md](../CONTRACT.md) §11 (`Persona.avatar`); [ROADMAP.md](../ROADMAP.md) §4

## Goal

Every persona has its own picture, made once (not per business), committed to the repo, and named in the templates. The app shows them on the dashboard and in Telegram's "Meet your team" message.

## Design

- **YAML:** `persona: { name: Maya, role: Marketing lead, avatar: marketing/maya.png }`. `Persona` is the contract model, so the field flows through `TemplateInfo`, `TeamHired` and every event without more code. Alex, the chief of staff, gets `avatar="company/alex.png"` in `graphs/company.py`.
- **Files:** `assets/avatars/{template}/{name}.png`, 512 × 512 PNG, under ~300 KB each. The `assets/` folder is shared like `docs/`, and the app serves it at `/avatars/`.
- **The loader checks that each named avatar file exists**; a missing file is a warning, not an error, so the brain never fails to start over a picture.
- **Making them:** `uv run python -m brain.avatars [--only marketing/maya]`. It runs once by hand, and its output is committed.
  - It uses `MODEL_IMAGE` through `helpers/images.py`, with one shared style prompt plus one subject per persona.
  - It writes the files and skips existing ones unless `--force`.
- **Style prompt:** "Flat vector illustration, round badge, soft off-white background, deep green (#2f5d50) and warm amber (#a8660b) palette on a light sage circle (#e2ece8), friendly, no text, centred character, simple shapes, consistent line weight." The palette matches the dashboard.

| Persona | Subject |
| --- | --- |
| Alex, Chief of staff | an owl holding a clipboard |
| Maya, Marketing lead | a fox with a small megaphone |
| Leo, Writer | a raven with a quill |
| Sam, Researcher | a small round robot with a magnifying glass |
| Iris, Design lead | a chameleon with a colour swatch |
| Otto, Illustrator | an octopus holding paint brushes |

- **Fallback:** if generation or the style doesn't come out consistent, Mark makes the six files by hand. The code only needs the paths.

## Files

| File | Contains |
| --- | --- |
| `src/brain/avatars.py` | the one-off generator (`python -m brain.avatars`) |
| `src/brain/templates/*.yaml` | `avatar` on every persona |
| `src/brain/graphs/company.py` | Alex's avatar |
| `src/brain/templates/loader.py` | warns about missing avatar files |
| `assets/avatars/**.png` | the pictures |

## Tests

`tests/brain/test_avatars.py`

- `test_every_persona_has_an_avatar_file`: every template persona and Alex have an `avatar` and the file exists.
- `test_avatars_reach_template_info_and_team_hired`
- `test_missing_avatar_file_only_warns`
- `test_generator_skips_existing_files` (with `FakeImages` and a temp dir)

## Done when

- [ ] All tests above pass
- [ ] Six pictures committed, in one consistent style
- [ ] Juan has reviewed the tests

## Log

- Oct 9: stage written by Mark from the v0.6 planning session.
