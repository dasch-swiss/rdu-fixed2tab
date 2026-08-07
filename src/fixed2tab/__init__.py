"""fixed2tab — convert fixed-width text files to tabular (TSV).

Column boundaries are detected automatically by finding character positions that
are blank in *every* record, so the common case needs no configuration at all.

The version below is the single source of truth. `pyproject.toml` reads it via
hatchling's `[tool.hatch.version]`, the conda recipe takes it from the published
sdist, and the Galaxy wrapper's `@TOOL_VERSION@` is checked against it in CI.
"""

__version__ = "0.1.0.dev0"

__all__ = ["__version__"]
