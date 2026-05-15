"""Reusable validators for the cstoolbox plugin."""

import re

from ckan.plugins import toolkit

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,99}$")


def valid_identifier(value):
    """SQL-ish identifier: letters/digits/underscore, must start with letter/_."""
    if not value:
        return value
    value = value.strip()
    if not _IDENT_RE.match(value):
        raise toolkit.Invalid(
            "Must start with a letter or underscore and contain only "
            "letters, digits or underscores (max 128 chars)."
        )
    return value


def valid_slug(value):
    """URL-safe slug: lowercase letters/digits/hyphens."""
    if not value:
        return value
    value = value.strip().lower()
    if not _SLUG_RE.match(value):
        raise toolkit.Invalid(
            "Slug must contain only lowercase letters, digits and hyphens."
        )
    return value
