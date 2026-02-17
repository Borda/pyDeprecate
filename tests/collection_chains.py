"""Collection of deprecated functions that target other deprecated functions.

This module contains test examples for validate_deprecation_chains functionality.

Deprecation Chain Schema:

    BAD — outer target is itself deprecated (chain detected):

    ╔════════════════════════════════════════╗
    ║ chain (2) | caller_sum_via_depr_sum    ║
    ║-----------|----------------------------║
    ║ target    |  depr_sum                  ║
    ╚════════════════════╤═══════════════════╝
                         │  [deprecated ← chain]
                         ▼
    ╔════════════════════════════════════════╗
    ║ chain (1) | depr_sum                   ║
    ║-----------|----------------------------║
    ║ target    |  base_sum_kwargs           ║
    ╚════════════════════╤═══════════════════╝
                         │
                         ▼
    ╔════════════════════════════════════════╗
    ║ final     | base_sum_kwargs            ║
    ╚════════════════════════════════════════╝

    ╔════════════════════════════════════════╗
    ║ chain (2) | caller_acc_via_depr_map    ║
    ║-----------|----------------------------║
    ║ target    |  depr_accuracy_map         ║
    ╚════════════════════╤═══════════════════╝
                         │  [deprecated ← chain]
                         ▼
    ╔════════════════════════════════════════╗
    ║ chain (1) | depr_accuracy_map          ║
    ║-----------|----------------------------║
    ║ target    |  accuracy_score            ║
    ║ mapping   |  "preds" -> "y_pred"       ║
    ║           |  "truth" -> "y_true"       ║
    ╚════════════════════╤═══════════════════╝
                         │
                         ▼
    ╔════════════════════════════════════════╗
    ║ final     | accuracy_score             ║
    ╚════════════════════════════════════════╝

    BAD — chained arg mappings (each hop renames args, must be collapsed):

    ╔════════════════════════════════════════╗
    ║ chain (2) | caller_acc_comp_depr_map   ║
    ║-----------|----------------------------║
    ║ target    |  depr_accuracy_map         ║
    ║ mapping   |  "predictions" -> "preds"  ║
    ║           |  "labels" -> "truth"       ║
    ╚════════════════════╤═══════════════════╝
                         │  [deprecated ← chain, mappings compose]
                         ▼
    ╔════════════════════════════════════════╗
    ║ chain (1) | depr_accuracy_map          ║
    ║-----------|----------------------------║
    ║ target    |  accuracy_score            ║
    ║ mapping   |  "preds" -> "y_pred"       ║
    ║           |  "truth" -> "y_true"       ║
    ╚════════════════════╤═══════════════════╝
                         │
                         ▼
    ╔════════════════════════════════════════╗
    ║ final     | accuracy_score             ║
    ╚════════════════════════════════════════╝
    # Fix: target=accuracy_score,
    #   args_mapping={"predictions": "y_pred", "labels": "y_true"}

    BAD — stacked self-deprecation arg mappings (should be collapsed):

    ╔════════════════════════════════════════╗
    ║ outer     | caller_stacked_args_map    ║
    ║-----------|----------------------------║
    ║ target    |  True (self)               ║
    ║ mapping   |  "c1" -> "nc2"             ║
    ╚════════════════════╤═══════════════════╝
                         │  [stacked ← chain]
                         ▼
    ╔════════════════════════════════════════╗
    ║ inner     | caller_stacked_args_map    ║
    ║-----------|----------------------------║
    ║ target    |  True (self)               ║
    ║ mapping   |  "nc1" -> "nc2"            ║
    ╚════════════════════════════════════════╝
    # Fix: single @deprecated(True, ..., args_mapping={"c1": "nc2", "nc1": "nc2"})

    BAD — callable target is itself a self-deprecation (mappings must compose):

    ╔════════════════════════════════════════╗
    ║ chain (2) | caller_pow_via_self_depr   ║
    ║-----------|----------------------------║
    ║ target    |  depr_pow_self             ║
    ║ mapping   |  "exp" -> "coef"           ║
    ╚════════════════════╤═══════════════════╝
                         │  [stacked ← chain, target is self-depr]
                         ▼
    ╔════════════════════════════════════════╗
    ║ self-depr | depr_pow_self              ║
    ║-----------|----------------------------║
    ║ target    |  True (self)               ║
    ║ mapping   |  "coef" -> "new_coef"      ║
    ╚════════════════════════════════════════╝
    # Fix: target=depr_pow_self.__wrapped__,
    #   args_mapping={"exp": "new_coef"}

    GOOD — outer target is not deprecated (no chain):

    ╔════════════════════════════════════════╗
    ║ chain (1) | caller_sum_direct          ║
    ║-----------|----------------------------║
    ║ target    |  base_sum_kwargs           ║
    ╚════════════════════╤═══════════════════╝
                         │
                         ▼
    ╔════════════════════════════════════════╗
    ║ final     | base_sum_kwargs            ║
    ╚════════════════════════════════════════╝

    Legend:
        chain (N) : deprecated function, N hops from the final target
                    (chain (1) = one hop away, chain (2) = two hops away, …)
        target    : forwarding destination set in @deprecated(target=...)
        mapping   : argument renames applied on forwarding (old -> new)
        final     : plain, non-deprecated function — chain ends here
        [deprecated ← chain]: target is itself deprecated (a callable with
                               @deprecated), detected by validate_deprecation_chains
        note      : target=True (self-deprecation for arg renaming) is NOT a chain
"""

from deprecate import deprecated, void
from tests.collection_deprecate import depr_accuracy_map, depr_pow_self, depr_sum
from tests.collection_targets import base_sum_kwargs


@deprecated(target=depr_sum, deprecated_in="1.5", remove_in="2.5")
def caller_sum_via_depr_sum(a: int, b: int = 5) -> int:
    """Deprecated wrapper whose target is itself deprecated (chain).

    Examples:
        Instead of pointing directly to ``base_sum_kwargs``, this wrapper
        routes through ``depr_sum`` (also deprecated). The outer wrapper
        should skip the intermediate step and target ``base_sum_kwargs`` directly.
    """
    return void(a, b)


@deprecated(target=depr_accuracy_map, deprecated_in="1.5", remove_in="2.5")
def caller_acc_via_depr_map(preds: list, truth: tuple = (0, 1, 1, 2)) -> float:
    """Deprecated wrapper chaining through a deprecated function with arg mapping.

    Examples:
        Routes through ``depr_accuracy_map`` (deprecated) instead of pointing
        directly to ``accuracy_score``. Both the intermediate step and its
        argument renaming should be collapsed into a direct target reference.
    """
    return void(preds, truth)


@deprecated(
    target=depr_accuracy_map,
    deprecated_in="1.5",
    remove_in="2.5",
    args_mapping={"predictions": "preds", "labels": "truth"},
)
def caller_acc_comp_depr_map(predictions: list, labels: tuple = (0, 1, 1, 2)) -> float:
    """Deprecated wrapper with its own arg mapping that chains into another deprecated mapping.

    Examples:
        This wrapper renames ``predictions``->``preds`` and ``labels``->``truth``, then
        forwards to ``depr_accuracy_map`` which further renames ``preds``->``y_pred``
        and ``truth``->``y_true`` before reaching ``accuracy_score``.

        To fix: collapse both hops into a single wrapper targeting ``accuracy_score``
        directly with ``args_mapping={"predictions": "y_pred", "labels": "y_true"}``.
    """
    return void(predictions, labels)


@deprecated(True, deprecated_in="0.3", remove_in="0.6", args_mapping={"c1": "nc2"})
@deprecated(True, deprecated_in="0.4", remove_in="0.7", args_mapping={"nc1": "nc2"})
def caller_stacked_args_map(base: int, c1: int = 0, nc1: int = 0, nc2: int = 2) -> int:
    """Stacked self-deprecation decorators whose arg mappings should be collapsed.

    Examples:
        Both decorators use ``target=True`` (self-deprecation) but each renames a
        different argument. They should be merged into a single decorator:
        ``@deprecated(True, ..., args_mapping={"c1": "nc2", "nc1": "nc2"})``.
    """
    return void(base, c1, nc1, nc2)


@deprecated(target=depr_pow_self, deprecated_in="1.5", remove_in="2.5", args_mapping={"exp": "coef"})
def caller_pow_via_self_depr(base: float, exp: float = 2) -> float:
    """Deprecated wrapper whose target is itself a self-deprecation with arg renaming.

    Examples:
        Routes through ``depr_pow_self`` (deprecated with ``target=True, args_mapping={"coef": "new_coef"}``).
        The two arg renames compose: ``exp -> coef -> new_coef``. The fix is to target the
        final implementation directly with the collapsed mapping ``{"exp": "new_coef"}``.
    """
    return void(base, exp)


@deprecated(target=base_sum_kwargs, deprecated_in="1.5", remove_in="2.5")
def caller_sum_direct(a: int, b: int = 3) -> int:
    """Deprecated wrapper with a clean, non-deprecated target (correct pattern).

    Examples:
        Points directly to ``base_sum_kwargs``, which is not deprecated.
        This is the correct pattern and should not trigger any chain warnings.
    """
    return void(a, b)
