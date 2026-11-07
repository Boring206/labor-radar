"""Photo → draft shifts and pay, via Gemini vision with a strict JSON schema.

The model only transcribes. Its output is parsed here into a typed draft that the
worker reviews in the form; the rule engine never sees model output directly.
"""

import base64
import os
from datetime import date, time

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODEL = "gemini-2.5-flash"
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "application/pdf"}
MAX_BYTES = 8 * 1024 * 1024

PROMPT = """你是資料轉錄員。圖片是台灣打工族的班表、打卡紀錄或薪資單。
只轉錄圖片上看得到的資料，不要推測、不要計算、不要判斷是否違法。
- 每一筆上班紀錄輸出一個 shift：date 用 YYYY-MM-DD，start/end 用 24 小時制 HH:MM。
- 圖上沒有年份時，用 {year} 年。
- 下班時間比上班早代表跨夜，照圖上的時間填，不要改日期。
- 看得到休息時間才填 break_minutes。
- 薪資單上的應發金額（扣勞健保前）填 gross_pay；看得到時薪或月薪才填。
- 看不清楚或無法判讀的地方寫進 unreadable，不要猜。"""

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "document_type": {"type": "STRING", "enum": ["schedule", "clock_records", "payslip", "other"]},
        "worker_name": {"type": "STRING", "nullable": True},
        "employer_name": {"type": "STRING", "nullable": True},
        "shifts": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "date": {"type": "STRING"},
                    "start": {"type": "STRING"},
                    "end": {"type": "STRING"},
                    "break_minutes": {"type": "INTEGER", "nullable": True},
                },
                "required": ["date", "start", "end"],
            },
        },
        "hourly_wage": {"type": "NUMBER", "nullable": True},
        "monthly_wage": {"type": "NUMBER", "nullable": True},
        "gross_pay": {"type": "NUMBER", "nullable": True},
        "pay_period_start": {"type": "STRING", "nullable": True},
        "pay_period_end": {"type": "STRING", "nullable": True},
        "unreadable": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["document_type", "shifts", "unreadable"],
}


class ExtractionError(RuntimeError):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


class DraftShift(BaseModel):
    model_config = ConfigDict(extra="ignore")
    date: date
    start: time
    end: time
    break_minutes: int | None = Field(None, ge=0, le=600)


class Draft(BaseModel):
    model_config = ConfigDict(extra="ignore")
    document_type: str
    worker_name: str | None = None
    employer_name: str | None = None
    shifts: list[DraftShift] = Field(max_length=1000)
    hourly_wage: float | None = Field(None, gt=0)
    monthly_wage: float | None = Field(None, gt=0)
    gross_pay: float | None = Field(None, ge=0)
    pay_period_start: date | None = None
    pay_period_end: date | None = None
    unreadable: list[str] = []


def available() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY"))


def extract(image: bytes, mime_type: str, year: int) -> Draft:
    if mime_type not in ALLOWED_TYPES:
        raise ExtractionError(f"unsupported file type {mime_type}", 415)
    if len(image) > MAX_BYTES:
        raise ExtractionError("file is larger than 8 MB", 413)
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise ExtractionError("GEMINI_API_KEY is not set", 503)
    body = {
        "contents": [{"parts": [
            {"text": PROMPT.format(year=year)},
            {"inline_data": {"mime_type": mime_type, "data": base64.b64encode(image).decode()}},
        ]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
        },
    }
    model = os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
    try:
        response = httpx.post(ENDPOINT.format(model=model), json=body, timeout=90,
                              headers={"x-goog-api-key": key})
        response.raise_for_status()
        text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        return Draft.model_validate_json(text)
    except httpx.HTTPStatusError as error:
        raise ExtractionError(f"Gemini returned HTTP {error.response.status_code}") from None
    except (httpx.HTTPError, KeyError, IndexError) as error:
        raise ExtractionError(f"Gemini call failed: {type(error).__name__}") from None
    except ValidationError as error:
        raise ExtractionError(f"Gemini output did not match the schema: {error.error_count()} problems") from None
