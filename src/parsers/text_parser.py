"""Plain-text (.txt) invoice parser.

Returns the raw file content as a single string.  Handles common encoding
issues gracefully — falls back to latin-1 if UTF-8 fails.
"""

import logging

logger = logging.getLogger(__name__)


def parse_text(file_path: str) -> str:
    """Read a text file and return its contents as a string.

    Args:
        file_path: Absolute or relative path to the .txt file.

    Returns:
        Raw text content, or an empty string on read failure.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as fh:
            return fh.read()
    except UnicodeDecodeError:
        logger.warning("UTF-8 decode failed for %s, retrying with latin-1", file_path)
        try:
            with open(file_path, "r", encoding="latin-1") as fh:
                return fh.read()
        except Exception as exc:
            logger.error("Failed to read %s: %s", file_path, exc)
            return ""
    except FileNotFoundError:
        logger.error("File not found: %s", file_path)
        return ""
    except Exception as exc:
        logger.error("Unexpected error reading %s: %s", file_path, exc)
        return ""
