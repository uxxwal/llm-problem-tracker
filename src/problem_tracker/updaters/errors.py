"""updaters/errors.py — shared exception types for the updater pipeline."""

__all__ = ["ExtractionError"]


class ExtractionError(Exception):
    """Raised by an Extractor when the LLM boundary fails to produce usable output.

    Corresponds to a failure of the extraction function E in the formal model:
    the turn could not be interpreted into operations, so the Applicator is
    never invoked and the state is left unchanged (identity update). The
    orchestrating updater records this as an ``extraction_failed`` log entry
    so failure rates remain measurable.
    """
