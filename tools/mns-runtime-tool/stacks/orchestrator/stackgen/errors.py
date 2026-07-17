"""Shared errors for the stack generator."""


class SimstackError(RuntimeError):
    """Raised when scenario input cannot be resolved into a runnable stack."""
