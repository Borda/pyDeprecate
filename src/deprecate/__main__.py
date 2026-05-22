"""Enables ``python -m deprecate`` as an alias for the ``pydeprecate`` CLI."""

from deprecate._cli import cli

if __name__ == "__main__":
    cli()
