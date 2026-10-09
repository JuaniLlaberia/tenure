import io
import logging

import yaml
from PIL import Image

from brain.avatars import SIZE, all_personas, generate_avatars, write_placeholders
from brain.fakes import FakeImages
from brain.graphs.company import CHIEF_OF_STAFF
from brain.templates.loader import AVATARS_DIR, TEMPLATES_DIR, load_templates
from contract import Ask, Persona, TeamHired

MAYA = Persona(name="Maya", role="Marketing lead", avatar="marketing/maya.png")
LEO = Persona(name="Leo", role="Writer", avatar="marketing/leo.png")

def size_of(path):
    return Image.open(io.BytesIO(path.read_bytes())).size

def test_every_persona_has_an_avatar_file():
    personas = all_personas(load_templates())

    assert CHIEF_OF_STAFF in personas
    assert {p.name for p in personas} >= {"Alex", "Maya", "Leo", "Sam", "Iris", "Otto"}
    for persona in personas:
        assert persona.avatar, f"{persona.name} has no avatar"
        path = AVATARS_DIR / persona.avatar
        assert path.is_file(), f"Missing {path}"
        assert size_of(path) == (SIZE, SIZE)
        assert path.stat().st_size < 300_000

async def test_avatars_reach_template_info_and_team_hired(brain, collect):
    info = {t.name: t for t in brain.list_templates()}["marketing"]
    assert [p.avatar for p in info.personas] == [
        "marketing/maya.png",
        "marketing/leo.png",
        "marketing/sam.png",
    ]

    hired = await collect(brain.hire_team("b1", "marketing"))
    (event,) = [e for e in hired if isinstance(e, TeamHired)]
    assert event.personas == info.personas

    started = await collect(brain.start_onboarding("b1"))
    (ask,) = [e for e in started if isinstance(e, Ask)]
    assert ask.persona.avatar == "company/alex.png"

def test_missing_avatar_file_only_warns(tmp_path, caplog):
    templates = tmp_path / "templates"
    templates.mkdir()
    for path in TEMPLATES_DIR.glob("*.yaml"):
        data = yaml.safe_load(path.read_text())
        if data["name"] == "marketing":
            data["lead"]["persona"]["avatar"] = "marketing/nobody.png"
        (templates / path.name).write_text(yaml.safe_dump(data))

    with caplog.at_level(logging.WARNING):
        loaded = load_templates(templates, avatars=AVATARS_DIR)

    assert loaded["marketing"].lead.persona.avatar == "marketing/nobody.png"
    assert any("marketing/nobody.png" in record.getMessage() for record in caplog.records)

async def test_generator_skips_existing_files(tmp_path):
    images = FakeImages()
    existing = tmp_path / "marketing" / "maya.png"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"hand made")

    written = await generate_avatars(images, "some/model", tmp_path, [MAYA, LEO])

    assert written == [tmp_path / "marketing" / "leo.png"]
    assert existing.read_bytes() == b"hand made"
    assert size_of(written[0]) == (SIZE, SIZE)
    (call,) = images.calls
    assert call["model"] == "some/model"
    assert "raven" in call["prompt"] and "Flat vector illustration" in call["prompt"]
    assert call["aspect_ratio"] == "1:1"

    forced = await generate_avatars(images, "some/model", tmp_path, [MAYA, LEO], force=True)
    assert len(forced) == 2 and existing.read_bytes() != b"hand made"

    only = await generate_avatars(
        images, "some/model", tmp_path, [MAYA, LEO], force=True, only="marketing/leo"
    )
    assert only == [tmp_path / "marketing" / "leo.png"]

def test_placeholders_are_badges_of_the_right_size(tmp_path):
    written = write_placeholders(tmp_path, [MAYA, LEO])

    assert sorted(path.name for path in written) == ["leo.png", "maya.png"]
    assert all(size_of(path) == (SIZE, SIZE) for path in written)
    assert write_placeholders(tmp_path, [MAYA, LEO]) == []
