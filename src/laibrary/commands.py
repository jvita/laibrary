"""Command classification for immediate vs queued processing."""


def is_immediate_command(user_input: str) -> bool:
    """Check if a user input is an immediate command (not queued).

    Immediate commands are processed synchronously because they are
    fast operations (project switching, listing, reading).

    Args:
        user_input: Raw user input string.

    Returns:
        True if the command should be processed immediately.
    """
    stripped = user_input.strip().lower()

    if stripped in ("/quit", "/status", "/clear", "/list", "/projects"):
        return True

    if stripped.startswith("/use "):
        return True

    if stripped == "/read" or stripped.startswith("/read "):
        return True

    # /<project> with no space = project switch
    if stripped.startswith("/") and " " not in stripped:
        return True

    return False
