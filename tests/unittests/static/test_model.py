"""Unit tests for the static source model."""

import pytest

from deprecate._static.model import _StaticTarget


class TestTargetSemantics:
    """``message-required`` reaches the same verdict from source as from the imported wrapper."""

    def test_static_target_is_never_called(self) -> None:
        """A replacement read from source is a named placeholder that refuses to run.

        It stands in for the real callable only so the policy rule and the report can see that a replacement is named;
        calling it would mean static mode executed something it never imported.

        """
        target = _StaticTarget("pkg.new")
        with pytest.raises(TypeError, match="was read from source by `--static`"):
            target()
        assert (target.__qualname__, target.__module__) == ("pkg.new", "")
