"""Proxy utilities for deprecating Python object instances and classes.

Provides :func:`~deprecate.proxy.deprecated_instance` for wrapping any Python object with
transparent deprecation warnings and optional read-only enforcement, and
:func:`~deprecate.proxy.deprecated_class` as a class-level decorator with optional target
redirection. Automatic class docstring injection preserves ``None`` when the wrapped class has no docstring.

The public factories and typing contracts live in :mod:`deprecate.proxy.factories`. Runtime and copy/pickle support
live in :mod:`deprecate.proxy.core`, target and attribute validation in :mod:`deprecate.proxy.resolve`, and protocol
forwarding in :mod:`deprecate.proxy.dunders`. The proxy classes and the pickle reconstruction helpers keep
``deprecate.proxy`` as their module and stay importable from it.

Typical use cases:

- Deprecating module-level config dicts, constants, or legacy singletons
  while still allowing reads during a migration window.
- Deprecating an Enum or dataclass in favor of a replacement type, with
  automatic forwarding of all attribute, item, and call access.

Both entry points return an object satisfying :class:`~deprecate._types.DeprecationProxy`, the public
protocol for proxies. Every proxy carries ``__wrapped__`` (the source object) and ``__signature__``
(the source's signature) so ``inspect.unwrap``, ``inspect.signature``, Sphinx autodoc, and IDEs
resolve through the proxy to the original — reading either attribute emits no deprecation warning.

Context entry releases an acquired resource if warning delivery raises. ``next`` warns before it advances the
iterator, so a failing delivery loses no item. Async next and await warnings fire when the underlying awaitable or
iterator is obtained, before deferred execution; if that delivery raises, a coroutine the acquisition created is
closed unstarted instead of being left unawaited.

Example:
    >>> import warnings
    >>> cfg = {"threshold": 0.5}
    >>> proxy = deprecated_instance(cfg, deprecated_in="1.0", remove_in="2.0", stream=None)
    >>> proxy["threshold"]
    0.5

    >>> proxy.get("threshold")
    0.5

"""

from deprecate._fatal import DeprecatedError as DeprecatedError
from deprecate._types import DeprecationConfig as DeprecationConfig
from deprecate._types import DeprecationProxy as DeprecationProxy
from deprecate._types import TargetMode as TargetMode
from deprecate._types import get_deprecation_config as get_deprecation_config
from deprecate.docstring.inject import normalize_docstring_style as normalize_docstring_style
from deprecate.messaging import TEMPLATE_ARGUMENT_MAPPING as TEMPLATE_ARGUMENT_MAPPING
from deprecate.messaging import TEMPLATE_WARNING_ARGUMENTS as TEMPLATE_WARNING_ARGUMENTS
from deprecate.messaging import TEMPLATE_WARNING_CALLABLE as TEMPLATE_WARNING_CALLABLE
from deprecate.messaging import TEMPLATE_WARNING_NO_TARGET as TEMPLATE_WARNING_NO_TARGET
from deprecate.messaging import deprecation_warning as deprecation_warning
from deprecate.proxy import dunders  # noqa: F401 # installs protocol forwarders
from deprecate.proxy.core import _BindingDeprecatedProxy as _BindingDeprecatedProxy
from deprecate.proxy.core import _DeprecatedProxy as _DeprecatedProxy
from deprecate.proxy.core import _proxy_call_global as _proxy_call_global
from deprecate.proxy.core import _proxy_type_for as _proxy_type_for
from deprecate.proxy.core import _ProxySpec as _ProxySpec
from deprecate.proxy.core import _reconstruct_proxy as _reconstruct_proxy
from deprecate.proxy.core import _restore_pep702_marker as _restore_pep702_marker
from deprecate.proxy.factories import deprecated_class as deprecated_class
from deprecate.proxy.factories import deprecated_instance as deprecated_instance
