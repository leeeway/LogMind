"""
Prompt Template Engine — Jinja2 Rendering + Variable Validation
"""

import json

import jsonschema
from jinja2 import BaseLoader, Environment, TemplateSyntaxError, UndefinedError

from logmind.core.exceptions import TemplateRenderError
from logmind.core.logging import get_logger
from logmind.domain.prompt.models import PromptTemplate

logger = get_logger(__name__)


class PromptEngine:
    """
    Prompt template rendering engine.

    - Validates variables against JSON Schema
    - Renders Jinja2 templates
    - Returns (system_prompt, user_prompt) tuple
    """

    def __init__(self):
        self._env = Environment(
            loader=BaseLoader(),
            autoescape=False,
            keep_trailing_newline=True,
        )

    def render(
        self, template: PromptTemplate, variables: dict
    ) -> tuple[str, str]:
        """
        Render a prompt template with the given variables.

        Args:
            template: PromptTemplate model instance
            variables: Dict of template variables

        Returns:
            Tuple of (system_prompt, user_prompt)

        Raises:
            TemplateRenderError: On validation or rendering failure
        """
        # 1. Validate variables against schema
        self._validate_variables(template, variables)

        # 2. Render system prompt
        try:
            system_prompt = self._render_string(template.system_prompt, variables)
        except Exception as e:
            raise TemplateRenderError(
                f"Failed to render system_prompt for template '{template.name}': {e}"
            )

        # 3. Render user prompt
        try:
            user_prompt = self._render_string(template.user_prompt_template, variables)
        except Exception as e:
            raise TemplateRenderError(
                f"Failed to render user_prompt for template '{template.name}': {e}"
            )

        logger.info(
            "prompt_rendered",
            template=template.name,
            system_len=len(system_prompt),
            user_len=len(user_prompt),
        )

        return system_prompt, user_prompt

    def _render_string(self, template_str: str, variables: dict) -> str:
        """Render a single Jinja2 template string."""
        try:
            tmpl = self._env.from_string(template_str)
            return tmpl.render(**variables)
        except UndefinedError as e:
            raise TemplateRenderError(f"Missing template variable: {e}")
        except TemplateSyntaxError as e:
            raise TemplateRenderError(f"Template syntax error: {e}")

    def _validate_variables(self, template: PromptTemplate, variables: dict) -> None:
        """Validate variables against the template's JSON Schema."""
        if not template.variables_schema or template.variables_schema == "{}":
            return

        try:
            schema = json.loads(template.variables_schema)
        except json.JSONDecodeError:
            logger.warning("invalid_variables_schema", template=template.name)
            return

        try:
            jsonschema.validate(instance=variables, schema=schema)
        except jsonschema.ValidationError as e:
            raise TemplateRenderError(
                f"Variable validation failed for template '{template.name}': "
                f"{e.message}"
            )

    def validate_template_syntax(self, template_str: str) -> list[str]:
        """
        Validate Jinja2 template syntax without rendering.

        Returns list of error messages (empty = valid).
        """
        errors = []
        try:
            self._env.parse(template_str)
        except TemplateSyntaxError as e:
            errors.append(f"Line {e.lineno}: {e.message}")
        return errors


# Singleton
prompt_engine = PromptEngine()
