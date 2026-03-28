"""Compatibilite CLI: redirige vers la V2."""

from .v2.cli import build_parser, main

__all__ = ["build_parser", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
