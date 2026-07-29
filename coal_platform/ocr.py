from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from io import BytesIO
from typing import Protocol

import pytesseract
from PIL import Image
from pytesseract import Output

from coal_platform.config import Settings


@dataclass(frozen=True)
class OCRLine:
    text: str
    confidence: float
    left: int
    top: int
    width: int
    height: int


class OCRBackend(Protocol):
    @property
    def engine_name(self) -> str: ...

    def recognize(self, image_content: bytes) -> list[OCRLine]: ...


class TesseractOCRBackend:
    def __init__(
        self,
        languages: str = "chi_sim+eng",
        timeout_seconds: int = 120,
        minimum_confidence: float = 0.35,
    ) -> None:
        self.languages = languages
        self.timeout_seconds = timeout_seconds
        self.minimum_confidence = minimum_confidence

    @property
    def engine_name(self) -> str:
        return f"tesseract:{self.languages}"

    def recognize(self, image_content: bytes) -> list[OCRLine]:
        with Image.open(BytesIO(image_content)) as image:
            data = pytesseract.image_to_data(
                image.convert("RGB"),
                lang=self.languages,
                config="--oem 1 --psm 6",
                output_type=Output.DICT,
                timeout=self.timeout_seconds,
            )
        grouped: dict[tuple[int, int, int], list[tuple[str, float, int, int, int, int]]] = defaultdict(list)
        for index, raw_text in enumerate(data["text"]):
            text = str(raw_text).strip()
            try:
                confidence = float(data["conf"][index]) / 100
            except (TypeError, ValueError):
                confidence = -1
            if not text or confidence < self.minimum_confidence:
                continue
            key = (int(data["block_num"][index]), int(data["par_num"][index]), int(data["line_num"][index]))
            grouped[key].append(
                (
                    text,
                    confidence,
                    int(data["left"][index]),
                    int(data["top"][index]),
                    int(data["width"][index]),
                    int(data["height"][index]),
                )
            )
        lines: list[OCRLine] = []
        for words in grouped.values():
            left = min(item[2] for item in words)
            top = min(item[3] for item in words)
            right = max(item[2] + item[4] for item in words)
            bottom = max(item[3] + item[5] for item in words)
            lines.append(
                OCRLine(
                    text=" ".join(item[0] for item in words),
                    confidence=round(sum(item[1] for item in words) / len(words), 4),
                    left=left,
                    top=top,
                    width=right - left,
                    height=bottom - top,
                )
            )
        return lines


def build_ocr_backend(settings: Settings) -> OCRBackend | None:
    if settings.ocr_backend == "disabled":
        return None
    return TesseractOCRBackend(
        languages=settings.ocr_languages,
        timeout_seconds=settings.ocr_timeout_seconds,
        minimum_confidence=settings.ocr_minimum_confidence,
    )
