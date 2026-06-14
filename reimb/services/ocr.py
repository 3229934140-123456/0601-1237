from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional


def perform_ocr_image(image_path: Path, lang: str = "chi_sim+eng") -> str:
    try:
        from PIL import Image
        import pytesseract
        img = Image.open(image_path)
        text = pytesseract.image_to_string(img, lang=lang)
        return text.strip()
    except ImportError:
        return _fallback_ocr_image(image_path)
    except Exception as e:
        return f"[OCR错误: {e}]"


def perform_ocr_pdf(pdf_path: Path, lang: str = "chi_sim+eng") -> str:
    try:
        from pdf2image import convert_from_path
        import pytesseract
        images = convert_from_path(str(pdf_path))
        texts = []
        for i, img in enumerate(images):
            text = pytesseract.image_to_string(img, lang=lang)
            texts.append(f"--- 第{i+1}页 ---\n{text.strip()}")
        return "\n\n".join(texts)
    except ImportError:
        return _fallback_ocr_pdf(pdf_path)
    except Exception as e:
        return _fallback_ocr_pdf(pdf_path, error=str(e))


def _fallback_ocr_image(image_path: Path, error: Optional[str] = None) -> str:
    prefix = f"[OCR错误: {error}]" if error else ""
    return f"{prefix}[模拟OCR] 图片文件: {image_path.name}"


def _fallback_ocr_pdf(pdf_path: Path, error: Optional[str] = None) -> str:
    prefix = f"[OCR错误: {error}]" if error else ""
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(str(pdf_path))
        texts = []
        for i, page in enumerate(reader.pages):
            t = page.extract_text()
            if t:
                texts.append(f"--- 第{i+1}页 ---\n{t.strip()}")
        if texts:
            return "\n\n".join(texts)
    except Exception:
        pass
    return f"{prefix}[模拟OCR] PDF文件: {pdf_path.name}"
