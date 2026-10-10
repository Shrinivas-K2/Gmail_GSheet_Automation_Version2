"""
app/domain/errors.py

Errors the rest of the program is allowed to know about.
"""


class DatabaseUnavailableError(Exception):
    """The database cannot be reached (wrong DATABASE_URL, server not running ...)."""
