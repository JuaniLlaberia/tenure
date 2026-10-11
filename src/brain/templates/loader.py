import logging
from pathlib import Path

import yaml
from pydantic import ValidationError

from brain.templates.models import Template
from contract import TemplateInfo

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent
AVATARS_DIR = Path(__file__).parents[3] / "assets" / "avatars"

class TemplateError(ValueError):
    pass

def load_template(path: Path) -> Template:
    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as error:
        raise TemplateError(f"{path.name}: {error}") from error
    if not isinstance(data, dict):
        raise TemplateError(f"{path.name}: expected a mapping at the top level")
    try:
        return Template.model_validate(data)
    except ValidationError as error:
        raise TemplateError(f"{path.name}: {error}") from error

def load_templates(
    directory: Path = TEMPLATES_DIR, avatars: Path = AVATARS_DIR
) -> dict[str, Template]:
    templates: dict[str, Template] = {}
    for path in sorted(directory.glob("*.yaml")):
        template = load_template(path)
        if template.name in templates:
            raise TemplateError(f"{path.name}: duplicate template name {template.name!r}")
        templates[template.name] = template
    _check_with_teams(templates)
    _check_avatars(templates, avatars)
    return templates

def _check_avatars(templates: dict[str, Template], avatars: Path) -> None:
    """
    A missing picture is only a warning: the brain never fails to start over one.
    """
    for template in templates.values():
        for persona in template.personas():
            if persona.avatar and not (avatars / persona.avatar).is_file():
                log.warning("%s: avatar file %s is missing", template.name, persona.avatar)

def _check_with_teams(templates: dict[str, Template]) -> None:
    """
    `with_teams` names steps in other templates, so it's checked once every template is loaded.
    """
    for template in templates.values():
        for task_type, spec in template.task_types.items():
            for other, specialist_ids in spec.with_teams.items():
                where = f"{template.name}.yaml, task type {task_type!r}"
                if other not in templates:
                    raise TemplateError(f"{where}: with_teams names unknown template {other!r}")
                unknown = [s for s in specialist_ids if s not in templates[other].specialists]
                if unknown:
                    raise TemplateError(
                        f"{where}: with_teams names unknown {other} specialists {unknown}"
                    )

def template_info(template: Template) -> TemplateInfo:
    return TemplateInfo(
        name=template.name,
        display_name=template.display_name,
        description=template.description,
        personas=template.personas(),
        task_types=list(template.task_types),
    )
