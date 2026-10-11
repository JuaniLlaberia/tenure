# Stage 13: Avatars

**Status:** done
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

- [x] All tests above pass
- [x] Six pictures made, in one consistent style (committed with the stage)
- [x] Juan has reviewed the tests

## Log

- Oct 9: stage written by Mark from the v0.6 planning session.
- Oct 9: reviewed by Juan, unchanged. The brain side creates and writes `assets/avatars/`. `test_every_persona_has_an_avatar_file` stays red until the six pictures exist (generated with the live key, or made by Mark).
- Oct 9: implemented on `brain-v06` under a session goal, tests first (5 red), then green. `tests/brain/test_avatars.py`: the 4 listed tests plus `test_placeholders_are_badges_of_the_right_size`. Choices made while building:
  - The six files in `assets/avatars/` are **placeholders**: round sage badges with the persona's initial, drawn locally by `python -m brain.avatars --placeholders` (Pillow, already a project dependency; no model call). Generating the real art spends money on the OpenRouter key (about $0.08 per picture with Nano Banana 2), so it waits for Juan: `uv run python -m brain.avatars --force` replaces them all, `--only marketing/maya` one.
  - The generator center-crops and resizes the model's 1K square to 512 × 512 PNG; unknown personas get "a friendly animal that stands for a <role>".
  - `AVATARS_DIR` (repo-root `assets/avatars/`) lives in `templates/loader.py`; `load_templates(..., avatars=...)` warns per missing file.
  - `FakeImages` now returns a valid 4×4 PNG, so the resize path can be tested.
  - The placeholders stay on `brain-v06` with the rest of the stage; push them to `dev` early if Mark needs files to serve before the merge.
- Oct 9: real pictures made by Juan with `python -m brain.avatars` (`google/gemini-3.1-flash-image`, first live call of the Images API client: request and response shapes held). The first two Mayas varied a lot (green vs orange fox, small badge, plants), so the style prompt now pins the badge filling the frame, a waist-up character in mainly deep green with amber accents, and no scenery. Files are saved as 256-colour palette PNGs (flat art looks the same; the first RGBA file was 324 KB, over the 300 KB limit). All six are 512 × 512, 147–173 KB. Otto (octopus) came out paler than the rest; re-roll with `--only design/otto` if wanted.
- Oct 9: Juan reviewed and approved the tests; stage done and committed on `brain-v06`.
