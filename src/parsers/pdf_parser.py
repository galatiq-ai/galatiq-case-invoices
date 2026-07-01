"""PDF invoice parser using pdfplumber.

Extracts all text from the PDF page by page and returns it as a single
string.  Falls back to PyMuPDF (fitz) if pdfplumber is unavailable.
"""

import logging

logger = logging.getLogger(__name__)


def parse_pdf(file_path: str) -> str:
    """Extract text from a PDF and return it as a single string.

    Args:
        file_path: Path to the .pdf file.

    Returns:
        Concatenated text from all pages, or empty string on failure.
    """
    try:
        import pdfplumber  # type: ignore

        with pdfplumber.open(file_path) as pdf:
            pages = []
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    pages.append(text)
            return "\n".join(pages)
    except ImportError:
        logger.warning("pdfplumber not installed; attempting PyMuPDF fallback")
        return _parse_pdf_pymupdf(file_path)
    except FileNotFoundError:
        logger.error("File not found: %s", file_path)
        return ""
    except Exception as exc:
        logger.error("PDF parse error for %s: %s", file_path, exc)
        return ""


def _parse_pdf_pymupdf(file_path: str) -> str:
    """Fallback PDF extractor using PyMuPDF (fitz)."""
    try:
        import fitz  # type: ignore

        doc = fitz.open(file_path)
        pages = [page.get_text() for page in doc]
        return "\n".join(pages)
    except ImportError:
        logger.error(
            "Neither pdfplumber nor PyMuPDF is installed. "
            "Install one: pip install pdfplumber  OR  pip install pymupdf"
        )
        return ""
    except Exception as exc:
        logger.error("PyMuPDF parse error for %s: %s", file_path, exc)
        return ""
