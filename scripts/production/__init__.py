"""Read-only production diagnostics, independent of the deployment executor."""


class InspectionError(ValueError):
    """Safe fixed diagnostic code; never include raw command or inventory output."""
