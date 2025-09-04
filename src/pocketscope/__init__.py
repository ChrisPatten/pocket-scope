"""PocketScope package root.

The project version is defined here as the single source of truth and
exposed via ``__version__``. The packaging configuration (pyproject.toml)
is configured to read this attribute using 
``version = { attr = "pocketscope.__version__" }``.
Update this value when making a manual version bump (until automated
tag-based versioning is introduced).
"""

__all__ = ["__version__"]

# Keep in sync with release tags until setuptools-scm or similar is adopted.
__version__ = "0.1.1"
