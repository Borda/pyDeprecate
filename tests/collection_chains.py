"""Collection of deprecated functions that target other deprecated functions.

This module contains test examples for validate_deprecation_chains functionality.

Deprecation Chain Schema:

    BAD — outer target is itself deprecated (chain detected):

    ╔════════════════════════════════════════╗
    ║ chain (2) | caller_chains_to_depr      ║
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
    ║ chain (2) | caller_chains_mapped_args  ║
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
    ║ chain (2) | caller_chains_composed_args║
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
    ║ outer     | caller_chains_stacked_args ║
    ║-----------|----------------------------║
    ║ target    |  True (self)               ║
    ║ mapping   |  "c1" -> "nc2"             ║
    ╚════════════════════╤═══════════════════╝
                         │  [stacked ← chain]
                         ▼
    ╔════════════════════════════════════════╗
    ║ inner     | caller_chains_stacked_args ║
    ║-----------|----------------------------║
    ║ target    |  True (self)               ║
    ║ mapping   |  "nc1" -> "nc2"            ║
    ╚════════════════════════════════════════╝
    # Fix: single @deprecated(True, ..., args_mapping={"c1": "nc2", "nc1": "nc2"})

    GOOD — outer target is not deprecated (no chain):

    ╔════════════════════════════════════════╗
    ║ chain (1) | caller_no_chain            ║
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
        outer/inner: stacked @deprecated(True) layers on the same function
        target    : forwarding destination set in @deprecated(target=...)
        mapping   : argument renames applied on forwarding (old -> new)
        final     : plain, non-deprecated function — chain ends here
        [deprecated ← chain]: target is itself deprecated,
                               detected by validate_deprecation_chains
        [stacked ← chain]: target=True layer wrapping another deprecated layer,
                           detected by validate_deprecation_chains
"""

from deprecate import deprecated, void
from tests.collection_deprecate import depr_accuracy_map, depr_sum
from tests.collection_targets import base_sum_kwargs


@deprecated(target=depr_sum, deprecated_in="1.5", remove_in="2.5")
def caller_chains_to_depr(a: int, b: int = 5) -> int:
    """Deprecated wrapper whose target is itself deprecated (chain).

    Examples:
        Instead of pointing directly to ``base_sum_kwargs``, this wrapper
        routes through ``depr_sum`` (also deprecated). The outer wrapper
        should skip the intermediate step and target ``base_sum_kwargs`` directly.
    """
    return void(a, b)


@deprecated(target=depr_accuracy_map, deprecated_in="1.5", remove_in="2.5")
def caller_chains_mapped_args(preds: list, truth: tuple = (0, 1, 1, 2)) -> float:
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
def caller_chains_composed_args(predictions: list, labels: tuple = (0, 1, 1, 2)) -> float:
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
def caller_chains_stacked_args(base: float, c1: float = 0, nc1: float = 0, nc2: float = 2) -> float:
    """Stacked self-deprecation that should be collapsed into a single decorator.

    Examples:
        The outer decorator renames ``c1``->``nc2`` and the inner renames ``nc1``->``nc2``.
        Both layers use ``target=True`` (self-deprecation), so the chain can be collapsed
        into a single ``@deprecated(True, ..., args_mapping={"c1": "nc2", "nc1": "nc2"})``.
    """
    return base**nc2


@deprecated(target=base_sum_kwargs, deprecated_in="1.5", remove_in="2.5")
def caller_no_chain(a: int, b: int = 3) -> int:
    """Deprecated wrapper with a clean, non-deprecated target (correct pattern).

    Examples:
        Points directly to ``base_sum_kwargs``, which is not deprecated.
        This is the correct pattern and should not trigger any chain warnings.
    """
    return void(a, b)
