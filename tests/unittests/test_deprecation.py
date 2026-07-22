"""Unit tests for the ``@deprecated`` front-door dispatch (:mod:`deprecate.deprecation`).

These tests mock the specialized target factories (``deprecated_callable`` for callables,
``deprecated_class`` for classes) so they assert only the *dispatch contract*: that the front door
routes each source shape to the correct factory and forwards the resolved arguments. The wrapper and
proxy behaviour that those factories produce is verified end-to-end elsewhere; here we isolate the
routing so a regression in argument forwarding (wrong target resolution, a dropped mapping) fails
loudly and cheaply without depending on the full machinery.
"""

import warnings
from unittest import mock

from deprecate import TargetMode, deprecated


class TestFrontDoorDispatchForwarding:
    """``@deprecated`` forwards each source shape to the right factory with the resolved arguments."""

    def test_callable_source_forwards_to_deprecated_callable(self) -> None:
        """A function source routes to ``deprecated_callable`` with ``AUTO`` resolved to ``NOTIFY``.

        A maintainer decorating a plain function with a bare ``@deprecated(...)`` (no ``target``, no mapping)
        expects the front door to hand the callable to the strict ``deprecated_callable`` factory and to have
        already turned the ``TargetMode.AUTO`` default into the concrete warn-only ``TargetMode.NOTIFY`` mode —
        ``AUTO`` must never leak through to the factory. Mocking the factory lets us assert exactly that,
        including that the version arguments are forwarded verbatim.

        """
        with mock.patch("deprecate.deprecation.deprecated_callable") as mock_callable:

            @deprecated(deprecated_in="1.0", remove_in="2.0")
            def old_func(x: int) -> int:
                return x

        mock_callable.assert_called_once()
        forwarded = mock_callable.call_args.kwargs
        assert forwarded["target"] is TargetMode.NOTIFY
        assert forwarded["deprecated_in"] == "1.0"
        assert forwarded["remove_in"] == "2.0"
        assert forwarded["args_mapping"] is None

    def test_callable_source_with_mapping_resolves_to_args_remap(self) -> None:
        """A function source with a bare ``args_mapping`` routes to ``deprecated_callable`` as ``ARGS_REMAP``.

        The convenience the ``AUTO`` default exists for: a maintainer who passes only ``args_mapping`` to
        ``@deprecated`` on a function expects the front door to infer ``TargetMode.ARGS_REMAP`` before handing
        the callable to ``deprecated_callable`` — and to forward the mapping untouched. Mocking the factory
        confirms the resolved mode and the preserved mapping without running the remap machinery.

        """
        with mock.patch("deprecate.deprecation.deprecated_callable") as mock_callable:

            @deprecated(args_mapping={"old_arg": "new_arg"}, deprecated_in="1.0", remove_in="2.0")
            def old_func(new_arg: int = 0) -> int:
                return new_arg

        forwarded = mock_callable.call_args.kwargs
        assert forwarded["target"] is TargetMode.ARGS_REMAP
        assert forwarded["args_mapping"] == {"old_arg": "new_arg"}

    def test_class_source_forwards_to_deprecated_class(self) -> None:
        """A class source routes to ``deprecated_class`` with ``AUTO`` forwarded as an unset ``target=None``.

        A maintainer decorating a class through the friendly front door expects dispatch to the class factory,
        not the callable one, and expects the ``TargetMode.AUTO`` default to arrive at ``deprecated_class`` as
        an unset ``target`` (``None``) so a supplied ``args_mapping`` auto-resolves exactly as it would in a
        direct ``deprecated_class(args_mapping=...)`` call. Mocking the proxy factory asserts the routing and
        the forwarded arguments; the one-time dispatch notice is silenced as noise for this contract test.

        """
        with mock.patch("deprecate.proxy.deprecated_class") as mock_class, warnings.catch_warnings():
            warnings.simplefilter("ignore")

            @deprecated(args_mapping={"old_arg": "new_arg"}, deprecated_in="1.0", remove_in="2.0")
            class OldClass:
                pass

        mock_class.assert_called_once()
        forwarded = mock_class.call_args.kwargs
        assert forwarded["target"] is None
        assert forwarded["args_mapping"] == {"old_arg": "new_arg"}
        assert forwarded["deprecated_in"] == "1.0"
        assert forwarded["remove_in"] == "2.0"
