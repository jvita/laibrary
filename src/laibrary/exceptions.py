"""Custom exceptions for the PKM system."""


class EditApplicationError(Exception):
    """Raised when an edit cannot be applied to a document."""

    def __init__(self, message: str, file_path: str, search_block: str):
        """Initialize the error."""
        self.file_path = file_path
        self.search_block = search_block
        super().__init__(message)
