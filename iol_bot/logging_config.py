import logging
import sys

from iol_bot.config import LOGS_DIR


class RedactCredentialsFilter(logging.Filter):
    """Evita que un access_token/refresh_token termine en el log por accidente."""

    SENSITIVE_KEYS = ("access_token", "refresh_token", "password")

    def filter(self, record):
        msg = record.getMessage()
        for key in self.SENSITIVE_KEYS:
            if key in msg.lower():
                record.msg = "[log omitido: contiene datos sensibles]"
                record.args = ()
                break
        return True


def setup_logging(level=logging.INFO):
    LOGS_DIR.mkdir(exist_ok=True)

    root = logging.getLogger("iol_bot")
    root.setLevel(level)
    root.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    console.addFilter(RedactCredentialsFilter())

    file_handler = logging.FileHandler(LOGS_DIR / "bot.log", encoding="utf-8")
    file_handler.setFormatter(fmt)
    file_handler.addFilter(RedactCredentialsFilter())

    root.addHandler(console)
    root.addHandler(file_handler)
    return root
