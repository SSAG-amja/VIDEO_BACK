class V3NotReadyError(RuntimeError):
    """Raised while the V3 package exists but its serving pipeline is not implemented."""
