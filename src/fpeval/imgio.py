"""Shared image loading for LLM calls. Re-exported so extraction and
classification cannot drift on format handling."""
from fpeval.imgclass import _b64 as encode_for_api, SUPPORTED  # noqa: F401
