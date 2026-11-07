"""Local web app: uv run uvicorn labor_radar.web:app --port 8730"""

import json
from datetime import date
from pathlib import Path

from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel, Field, ValidationError, model_validator

from labor_radar import extraction, report
from labor_radar.agent import questions
from labor_radar.agent.case import Case, Phase, fact_table
from labor_radar.agent.llm import LLM, GeminiLLM
from labor_radar.agent.loop import TurnResult, run_turn
from labor_radar.agent.questions import Tap
from labor_radar.agent.tools import Attachment
from labor_radar.engine import assess
from labor_radar.i18n import CATALOGS, REFERENCE, languages, resolve
from labor_radar.intake import IntakeError, parse_record

HERE = Path(__file__).parent
SAMPLE = HERE.parent / "samples" / "lin_xiaomei.json"

app = FastAPI(title="勞權雷達", docs_url="/api/docs")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(HERE / "static" / "index.html")


@app.get("/api/meta")
def meta() -> dict:
    return {
        "languages": languages(),
        "gemini": extraction.available(),
        "agent": extraction.available(),
        "sample": json.loads(SAMPLE.read_text(encoding="utf-8")),
    }


@app.get("/api/i18n/{lang}")
def catalog(lang: str) -> dict:
    return CATALOGS[REFERENCE] | CATALOGS[resolve(lang)]


def _assess(record: dict):
    try:
        return assess(parse_record(record))
    except IntakeError as error:
        raise HTTPException(status_code=422, detail=error.problems) from None


@app.post("/api/check")
def check(record: dict = Body(...), lang: str = Query(REFERENCE)) -> dict:
    return report.render(_assess(record), lang)


@app.post("/api/check.md", response_class=PlainTextResponse)
def check_markdown(record: dict = Body(...), lang: str = Query(REFERENCE)) -> str:
    return report.to_markdown(report.render(_assess(record), lang))


@app.post("/api/extract")
async def extract(file: UploadFile = File(...)) -> dict:
    data = await file.read()
    try:
        draft = extraction.extract(data, file.content_type or "", date.today().year)
    except extraction.ExtractionError as error:
        raise HTTPException(status_code=error.status, detail=str(error)) from None
    return draft.model_dump(mode="json")


class TurnIn(BaseModel):
    case: Case = Field(default_factory=Case)
    message: str = Field("", max_length=2000)
    tap: Tap | None = None
    lang: str = Field(REFERENCE, max_length=20)

    @model_validator(mode="after")
    def _one_input(self) -> "TurnIn":
        if bool(self.message.strip()) == (self.tap is not None):
            raise ValueError("send either a message or a tapped option")
        return self


class ConfirmIn(BaseModel):
    case: Case


def get_llm() -> LLM:
    if not extraction.available():
        raise HTTPException(status_code=503, detail="GEMINI_API_KEY is not set, so the AI assistant is off")
    return GeminiLLM()


def _untrusted_turn(raw: str) -> TurnIn:
    try:
        return TurnIn.model_validate_json(raw)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=[
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in error.errors()
        ]) from None


@app.post("/api/agent/turn")
def agent_turn(payload: str = Form(...), file: UploadFile | None = File(None),
               llm: LLM = Depends(get_llm)) -> TurnResult:
    turn = _untrusted_turn(payload)
    attachment = None
    if file is not None:
        data = file.file.read(extraction.MAX_BYTES + 1)
        if len(data) > extraction.MAX_BYTES:
            raise HTTPException(status_code=413, detail="file is larger than 8 MB")
        attachment = Attachment(data, file.content_type or "", file.filename or "")
    return run_turn(turn.case, turn.tap or turn.message, llm, turn.lang, attachment)


@app.post("/api/agent/confirm")
def agent_confirm(body: ConfirmIn, lang: str = Query(REFERENCE)) -> dict:
    if questions.phase(body.case) is not Phase.CONFIRM:
        raise HTTPException(status_code=409, detail="the facts cannot be confirmed yet, or are already confirmed")
    case = body.case.model_copy(update={"confirmed": True, "reported": False})
    return {"case": case, "phase": questions.phase(case),
            "facts": [row.as_dict() for row in fact_table(case, resolve(lang))]}
