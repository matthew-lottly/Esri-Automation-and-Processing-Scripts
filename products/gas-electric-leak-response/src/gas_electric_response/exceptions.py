class ConfigError(RuntimeError):
    """Missing or invalid configuration (.env / YAML)."""


class StageRejected(Exception):
    """A pipeline stage decided the submission must not continue (bad data, out of area)."""

    def __init__(self, stage: str, reason: str):
        super().__init__(f"{stage}: {reason}")
        self.stage, self.reason = stage, reason


class StoreError(RuntimeError):
    """A feature-service read/write failed."""
