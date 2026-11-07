"""Local web app: uv run uvicorn labor_radar.web:app --port 8730"""

import json
from datetime import date
from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from labor_radar import extraction, report
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
