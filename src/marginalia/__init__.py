"""marginalia — Markdown vault quality scanner for Obsidian, academics, and documentation teams."""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("marginalia")
except PackageNotFoundError:
    # package not installed (e.g. running from source without pip install -e .)
    __version__ = "0.0.0-dev"
