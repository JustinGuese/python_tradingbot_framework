import logging
import sys
from typing import Optional


def setup_logging(
    level: int = logging.INFO,
    log_file: Optional[str] = None,
    include_timestamp: bool = True
) -> None:
    """
    Setup centralized logging for the trading bot framework.
    
    Args:
        level: Logging level (default: logging.INFO)
        log_file: Optional path to a log file
        include_timestamp: Whether to include timestamps in the logs
    """
    # Create format string
    if include_timestamp:
        fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        datefmt = "%Y-%m-%d %H:%M:%S"
    else:
        fmt = "%(name)s - %(levelname)s - %(message)s"
        datefmt = None

    # Configure root logger
    root_logger = logging.getLogger()
    
    # Avoid duplicate handlers if setup_logging is called multiple times
    if root_logger.hasHandlers():
        root_logger.handlers.clear()
        
    root_logger.setLevel(level)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    root_logger.addHandler(console_handler)

    # File handler (if requested)
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
        root_logger.addHandler(file_handler)

    # Set external libraries to higher levels to reduce noise
    logging.getLogger("yfinance").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
    logging.getLogger("peewee").setLevel(logging.WARNING)
