class NotFoundError(Exception):
    """Resource not found."""

class OverAllocationError(Exception):
    """Assignment would over-allocate the team member."""

class ConflictWithLeaveError(Exception):
    """Assignment conflicts with approved leave."""

class ValidationError(Exception):
    """Business validation failed."""


def user_message(exc: Exception, db=None) -> str:
    """Text that is safe to show a user for an exception caught in a form handler.

    Our own business errors are shown as-is and input-validation errors are summarised.
    Anything else is logged with its traceback and replaced by a generic message, and the
    DB session (if given) is rolled back so the page can still be re-rendered.
    """
    import logging

    from pydantic import ValidationError as PydanticValidationError

    if isinstance(exc, (NotFoundError, OverAllocationError, ConflictWithLeaveError, ValidationError)):
        return str(exc)
    if isinstance(exc, PydanticValidationError):
        return "; ".join(f"{err['loc'][-1] if err['loc'] else 'input'}: {err['msg']}" for err in exc.errors())
    logging.getLogger("app").exception("Unexpected error while handling a request")
    if db is not None:
        db.rollback()
    return "Something went wrong. Please try again or contact support."
