"""
Makes the persona pictures once, by hand: `uv run python -m brain.avatars [--only marketing/maya]
[--force] [--placeholders]`. The output is committed to assets/avatars/.
"""

import argparse
import asyncio
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from brain.deps import Settings
from brain.graphs.company import CHIEF_OF_STAFF
from brain.helpers.images import ImageClient, OpenRouterImages
from brain.templates.loader import AVATARS_DIR, load_templates
from brain.templates.models import Template
from contract import Persona

SIZE = 512
STYLE = (
    "Flat vector illustration, round badge, soft off-white background, deep green (#2f5d50) and "
    "warm amber (#a8660b) palette on a light sage circle (#e2ece8), friendly, no text, centred "
    "character, simple shapes, consistent line weight. The sage circle fills almost the whole "
    "square, with a thin deep green outline. The character fills most of the circle, shown from "
    "the knees or waist up, drawn mainly in deep green with amber accents and off-white details. "
    "No ground, plants, scenery or other objects besides the one named."
)
SUBJECTS = {
    "company/alex.png": "an owl holding a clipboard",
    "marketing/maya.png": "a fox with a small megaphone",
    "marketing/leo.png": "a raven with a quill",
    "marketing/sam.png": "a small round robot with a magnifying glass",
    "design/iris.png": "a chameleon with a colour swatch",
    "design/otto.png": "an octopus holding paint brushes",
}
OFF_WHITE = (247, 245, 240)
SAGE = (226, 236, 232)
GREEN = (47, 93, 80)
AMBER = (168, 102, 11)

def all_personas(templates: dict[str, Template]) -> list[Persona]:
    """
    Every persona that needs a picture: the chief of staff, then each template's team.
    """
    personas = [CHIEF_OF_STAFF]
    for template in templates.values():
        personas.extend(template.personas())
    return personas

def _pending(
    directory: Path, personas: list[Persona], force: bool, only: str | None
) -> list[tuple[Persona, Path]]:
    pending = []
    for persona in personas:
        if not persona.avatar:
            continue
        if only and persona.avatar.removesuffix(".png") != only.removesuffix(".png"):
            continue
        path = directory / persona.avatar
        if path.exists() and not force:
            continue
        pending.append((persona, path))
    return pending

def subject(persona: Persona) -> str:
    return SUBJECTS.get(persona.avatar or "", f"a friendly animal that stands for a {persona.role}")

async def generate_avatars(
    images: ImageClient,
    model: str,
    directory: Path,
    personas: list[Persona],
    force: bool = False,
    only: str | None = None,
) -> list[Path]:
    """
    One picture per persona with the image model, in one shared style. Skips files that exist
    unless `force`; `only` picks one ("marketing/maya").
    """
    written = []
    for persona, path in _pending(directory, personas, force, only):
        image = await images.generate(model, f"{STYLE} Subject: {subject(persona)}.", "1:1")
        _write(path, _square(image.data))
        written.append(path)
    return written

def write_placeholders(
    directory: Path, personas: list[Persona], force: bool = False, only: str | None = None
) -> list[Path]:
    """
    Plain badges with the persona's initial, drawn locally (no model call), until the real
    pictures are made.
    """
    written = []
    for persona, path in _pending(directory, personas, force, only):
        _write(path, _badge(persona.name[:1].upper()))
        written.append(path)
    return written

def _square(data: bytes) -> bytes:
    """
    Center-crops to a square, resizes to SIZE and keeps 256 colours: flat art looks the same
    and the file stays well under 300 KB.
    """
    image = Image.open(io.BytesIO(data)).convert("RGB")
    side = min(image.size)
    left, top = (image.width - side) // 2, (image.height - side) // 2
    image = image.crop((left, top, left + side, top + side)).resize((SIZE, SIZE), Image.LANCZOS)
    return _png(image.quantize(colors=256, method=Image.Quantize.MEDIANCUT))

def _badge(initial: str) -> bytes:
    image = Image.new("RGB", (SIZE, SIZE), OFF_WHITE)
    draw = ImageDraw.Draw(image)
    margin = SIZE // 16
    draw.ellipse((margin, margin, SIZE - margin, SIZE - margin), fill=SAGE, outline=GREEN, width=12)
    font = ImageFont.load_default(size=SIZE // 2)
    draw.text((SIZE / 2, SIZE / 2), initial, fill=AMBER, font=font, anchor="mm")
    return _png(image)

def _png(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, "PNG", optimize=True)
    return out.getvalue()

def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)

async def run(args: argparse.Namespace) -> list[Path]:
    personas = all_personas(load_templates())
    if args.placeholders:
        return write_placeholders(AVATARS_DIR, personas, args.force, args.only)
    settings = Settings.from_env()
    if settings.openrouter_api_key is None:
        raise SystemExit("Set OPENROUTER_API_KEY in .env first, or use --placeholders.")
    images = OpenRouterImages(settings)
    return await generate_avatars(
        images, settings.model_image, AVATARS_DIR, personas, args.force, args.only
    )

def main() -> None:
    parser = argparse.ArgumentParser(description="Make the persona pictures in assets/avatars/.")
    parser.add_argument("--only", help='one picture, e.g. "marketing/maya"')
    parser.add_argument("--force", action="store_true", help="replace existing pictures")
    parser.add_argument(
        "--placeholders", action="store_true", help="draw plain badges locally, no model call"
    )
    written = asyncio.run(run(parser.parse_args()))
    for path in written:
        print(f"wrote {path}")
    if not written:
        print("Nothing to do: every picture exists (use --force to replace them).")

if __name__ == "__main__":
    main()
