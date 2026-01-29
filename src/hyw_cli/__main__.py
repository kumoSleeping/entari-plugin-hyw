"""
Entry point for running hyw_cli as a module.

Usage:
    python -m hyw_cli --help
    python -m hyw_cli query "your question"
"""

from hyw_cli.cli import cli

if __name__ == "__main__":
    cli()
