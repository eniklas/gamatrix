"""Discovery and validation helpers for deployment-selectable UX templates.

This module inspects the shipped template directories under ``src/gamatrix/
templates`` and treats explicit marker files there as the source of truth for
valid ``UX_TEMPLATE`` values. The helpers normalize user-supplied names to the
canonical on-disk directory name and fail fast when a configured template is not
present in the shipped product.
"""

from __future__ import annotations

from pathlib import Path

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_TEMPLATES_DIR = Path(__file__).parent / "static" / "templates"
DEFAULT_UX_TEMPLATE = "default"
VALID_TEMPLATE_MARKER = "valid_template"


def _is_ux_template_dir(path: Path) -> bool:
    """Return whether ``path`` looks like a shipped authenticated UX template.

    Valid UX template directories are real subdirectories of ``TEMPLATES_DIR``
    that opt in via an explicit marker file. This keeps discovery tied to
    shipped assets without inferring validity from incidental file names or
    directory naming conventions.
    """

    return path.is_dir() and (path / VALID_TEMPLATE_MARKER).is_file()


def discover_ux_templates(template_root: Path = TEMPLATES_DIR) -> tuple[str, ...]:
    """Return the canonical UX template names shipped with the application."""

    template_names = tuple(
        sorted(
            path.name for path in template_root.iterdir() if _is_ux_template_dir(path)
        )
    )
    if not template_names:
        raise RuntimeError(f"No UX templates found under {template_root}")
    return template_names


def canonicalize_ux_template_name(
    template_name: str, template_root: Path = TEMPLATES_DIR
) -> str:
    """Normalize ``template_name`` to a discovered canonical directory name.

    Matching is case-insensitive so deploy-time or environment configuration can
    use ``MODERN`` and still resolve to the shipped ``modern`` directory. Any
    value that does not correspond to a discovered template directory is
    rejected with the current valid options listed in the error.
    """

    if not isinstance(template_name, str):
        raise ValueError("ux_template must be a string")

    valid_templates = discover_ux_templates(template_root)
    valid_by_casefold = {name.casefold(): name for name in valid_templates}
    canonical_name = valid_by_casefold.get(template_name.casefold())
    if canonical_name is None:
        valid_list = ", ".join(valid_templates)
        raise ValueError(
            "ux_template must match one of the shipped template directories: "
            f"{valid_list}"
        )
    return canonical_name


UX_TEMPLATES = discover_ux_templates()
