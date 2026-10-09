from pathlib import Path

import yaml
from pydantic import ValidationError

from brain.templates.models import Template
from contract import TemplateInfo

TEMPLATES_DIR = Path(__file__).parent

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

def load_templates(directory: Path = TEMPLATES_DIR) -> dict[str, Template]:
    templates: dict[str, Template] = {}
    for path in sorted(directory.glob("*.yaml")):
        template = load_template(path)
        if template.name in templates:
            raise TemplateError(f"{path.name}: duplicate template name {template.name!r}")
        templates[template.name] = template
    return templates

def template_info(template: Template) -> TemplateInfo:
    return TemplateInfo(
        name=template.name,
        display_name=template.display_name,
        description=template.description,
        personas=template.personas(),
        task_types=list(template.task_types),
    )
