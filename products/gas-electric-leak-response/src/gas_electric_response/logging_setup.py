import logging

from rich.logging import RichHandler


class LoggingConfigurator:
    """One place that decides how logs look."""

    @staticmethod
    def setup(level: str = "INFO", rich: bool = True) -> None:
        handler = RichHandler(show_path=False, rich_tracebacks=True, markup=False) if rich else logging.StreamHandler()
        logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format="%(message)s" if rich else "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                            datefmt="[%X]", handlers=[handler], force=True)
        for noisy in ("weasyprint", "fontTools", "fontTools.subset", "PIL", "matplotlib"):      # third-party chatter
            logging.getLogger(noisy).setLevel(logging.ERROR)
