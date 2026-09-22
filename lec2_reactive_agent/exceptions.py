class AlertNotFound(Exception):
    """No alert matched the selector.

    Raised rather than falling back to an arbitrary alert: a run that silently
    investigates something other than what was asked for is unreproducible.
    """

class PathEscapeException(Exception):
    """A requested path resolved outside the workspace root."""

class ToolFault(Exception):
    """A tool failed.

    Raised by a tool for I/O failure, a missing file, or a malformed argument.
    The dispatcher turns it into a failed observation rather than letting it end
    the run: a fault the model can see is evidence it can act on.
    """
