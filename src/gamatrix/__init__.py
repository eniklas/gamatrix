"""gamatrix: compare game libraries across users via GOG Galaxy databases."""

from importlib.metadata import PackageNotFoundError, version

try:
    # Set at build time by setuptools_scm from the latest git tag.
    __version__ = version("gamatrix")
except PackageNotFoundError:  # running from a source tree that isn't installed
    __version__ = "0.0.0+unknown"
