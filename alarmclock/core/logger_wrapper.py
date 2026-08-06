"""
Logger wrapper with colored and formatted output for info/debug messages.
"""

import logging
import sys
from pathlib import Path
from typing import Optional

LOG_FILE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "daemon.log"

# ANSI color codes for formatting
class Colors:
    # Regular colors
    BLACK = '\033[30m'
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN = '\033[36m'
    WHITE = '\033[37m'

    # Bright colors
    BRIGHT_BLACK = '\033[90m'
    BRIGHT_RED = '\033[91m'
    BRIGHT_GREEN = '\033[92m'
    BRIGHT_YELLOW = '\033[93m'
    BRIGHT_BLUE = '\033[94m'
    BRIGHT_MAGENTA = '\033[95m'
    BRIGHT_CYAN = '\033[96m'
    BRIGHT_WHITE = '\033[97m'

    # Background colors
    BG_BLACK = '\033[40m'
    BG_RED = '\033[41m'
    BG_GREEN = '\033[42m'
    BG_YELLOW = '\033[43m'
    BG_BLUE = '\033[44m'
    BG_MAGENTA = '\033[45m'
    BG_CYAN = '\033[46m'
    BG_WHITE = '\033[47m'

    # Styles
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    REVERSE = '\033[7m'

    # Reset
    RESET = '\033[0m'

class ColoredFormatter(logging.Formatter):
    """Custom formatter with colors for different log levels."""

    # Define colors for different log levels
    LEVEL_COLORS = {
        'DEBUG': Colors.BRIGHT_CYAN,
        'INFO': Colors.BRIGHT_GREEN,
        'WARNING': Colors.BRIGHT_YELLOW,
        'ERROR': Colors.BRIGHT_RED,
        'CRITICAL': Colors.BRIGHT_MAGENTA
    }

    # Define formats for different log levels (with timestamp)
    LEVEL_FORMATS = {
        'DEBUG': f'{Colors.BRIGHT_CYAN}%(levelname)s{Colors.RESET} %(message)s',
        'INFO': f'{Colors.BRIGHT_GREEN}%(levelname)s{Colors.RESET} %(message)s',
        'WARNING': f'{Colors.BRIGHT_YELLOW}%(levelname)s{Colors.RESET} %(message)s',
        'ERROR': f'{Colors.BRIGHT_RED}%(levelname)s{Colors.RESET} %(message)s',
        'CRITICAL': f'{Colors.BRIGHT_MAGENTA}%(levelname)s{Colors.RESET} %(message)s'
    }

    def __init__(self, use_colors: bool = True):
        super().__init__()
        self.use_colors = use_colors

    def format(self, record):
        # Store original levelname
        original_levelname = record.levelname

        # Get module name if provided
        module_name = getattr(record, 'module_name', '')

        # Set the format based on log level - include timestamp in all formats
        if self.use_colors:
            if module_name:
                formatter = logging.Formatter(f'%(asctime)s [{Colors.BRIGHT_WHITE}{module_name}{Colors.RESET}] {self.LEVEL_FORMATS.get(original_levelname, "%(levelname)s %(message)s")}')
            else:
                formatter = logging.Formatter(f'%(asctime)s {self.LEVEL_FORMATS.get(original_levelname, "%(levelname)s: %(message)s")}')
        else:
            if module_name:
                formatter = logging.Formatter('%(asctime)s [%(module_name)s] %(levelname)s %(message)s')
            else:
                formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')

        # Format the record
        formatted_record = formatter.format(record)

        return formatted_record

def _file_handler() -> logging.FileHandler:
    """Build a FileHandler writing to LOG_FILE_PATH, without ANSI colors."""
    LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_FILE_PATH)
    handler.setFormatter(ColoredFormatter(use_colors=False))
    return handler


class LoggerWrapper:
    """Wrapper around Python's logging module with enhanced formatting."""

    def __init__(self, name: str, use_colors: bool = True):
        self.logger = logging.getLogger(name)
        self.use_colors = use_colors

        # Set default level to DEBUG
        self.logger.setLevel(logging.DEBUG)

        # Create console handler if not exists
        if not self.logger.handlers:
            console_handler = logging.StreamHandler(sys.stdout)

            # Set formatter with colors - match standard Python logging format
            formatter = ColoredFormatter(use_colors=use_colors)
            console_handler.setFormatter(formatter)

            self.logger.addHandler(console_handler)
            self.logger.addHandler(_file_handler())

    def _log_with_module(self, level: int, message: str, *args, module_name: str = None, **kwargs):
        """Internal method to log a message with optional module name."""
        if module_name:
            # Pass module_name as extra parameter so the formatter can access it
            self.logger.log(level, message, *args, extra={'module_name': module_name}, **kwargs)
        else:
            self.logger.log(level, message, *args, **kwargs)

    def debug(self, message: str, *args, module_name: str = None, **kwargs):
        """Log a debug message."""
        self._log_with_module(logging.DEBUG, message, *args, module_name=module_name, **kwargs)

    def info(self, message: str, *args, module_name: str = None, **kwargs):
        """Log an info message."""
        self._log_with_module(logging.INFO, message, *args, module_name=module_name, **kwargs)

    def warning(self, message: str, *args, module_name: str = None, **kwargs):
        """Log a warning message."""
        self._log_with_module(logging.WARNING, message, *args, module_name=module_name, **kwargs)

    def error(self, message: str, *args, module_name: str = None, **kwargs):
        """Log an error message."""
        self._log_with_module(logging.ERROR, message, *args, module_name=module_name, **kwargs)

    def critical(self, message: str, *args, module_name: str = None, **kwargs):
        """Log a critical message."""
        self._log_with_module(logging.CRITICAL, message, *args, module_name=module_name, **kwargs)

    def set_level(self, level: int):
        """Set the logging level."""
        self.logger.setLevel(level)

    def enable_colors(self):
        """Enable colored output."""
        self.use_colors = True

    def disable_colors(self):
        """Disable colored output."""
        self.use_colors = False


class _ModuleNameFilter(logging.Filter):
    """Tags every record passing through with a fixed module_name, so a
    third-party logger's own records (which never pass module_name=...)
    still get the "[name]" prefix ColoredFormatter uses for the rest of
    the app."""

    def __init__(self, module_name: str):
        super().__init__()
        self.module_name = module_name

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "module_name"):
            record.module_name = self.module_name
        return True


def configure_external_logger(name: str, module_name: str, use_colors: bool = True) -> None:
    """Route a third-party logger (e.g. uvicorn's "uvicorn"/"uvicorn.access")
    through the same timestamped ColoredFormatter as the rest of the app.
    Without this, uvicorn.Config installs its own logging config on those
    loggers, which prints as bare "INFO:     message" instead of matching
    our format."""
    external_logger = logging.getLogger(name)
    external_logger.handlers = []
    external_logger.propagate = False

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(ColoredFormatter(use_colors=use_colors))
    console_handler.addFilter(_ModuleNameFilter(module_name))
    external_logger.addHandler(console_handler)

    file_handler = _file_handler()
    file_handler.addFilter(_ModuleNameFilter(module_name))
    external_logger.addHandler(file_handler)


# Global instance for convenience
logger = LoggerWrapper(__name__)

# Example usage:
if __name__ == "__main__":
    # Test the logger with different levels
    logger.info("This is an info message")
    logger.debug("This is a debug message")
    logger.warning("This is a warning message")
    logger.error("This is an error message")
    logger.critical("This is a critical message")