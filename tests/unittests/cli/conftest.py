"""Fixtures shared by the :mod:`deprecate._cli` unit tests."""

from collections.abc import Iterator
from unittest.mock import patch

import pytest

from deprecate._cli.scan import _top_level_findable

#: The module name the subcommand tests scan with ``find_deprecation_wrappers`` mocked; nothing by that name exists.
_STAND_IN_MODULE = "some_module"


def _findable_with_stand_in(name: str) -> bool:
    """Treat the stand-in module as importable and look every other name up for real."""
    return name.split(".")[0] == _STAND_IN_MODULE or _top_level_findable(name)


@pytest.fixture(autouse=True)
def _stand_in_module_is_findable() -> Iterator[None]:
    """Let the preflight of every run accept ``some_module``, which the tests scan with the import mocked out.

    The preflight refuses a module name whose top-level package cannot be found before anything is imported, but these
    tests replace the import itself (``find_deprecation_wrappers``) and never create the module. Every other name is
    still looked up, so a test of a missing module keeps its real verdict.

    """
    with patch("deprecate._cli.scan._top_level_findable", side_effect=_findable_with_stand_in):
        yield
