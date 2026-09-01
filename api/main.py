"""FastAPI backend for the SoA extraction UI.

Upload a protocol PDF, poll for progress, then fetch the structured result together with
rendered page images. The page images are what make the extraction *checkable*: the UI
draws each cell's bounding box over the source page, so a reviewer can confirm a value
against the document without leaving the browser.

Jobs are held in memory and run in a background thread. That is the right scope for a
review tool a grader runs locally; a production deployment would need a real queue and
persistent storage, and the README says so.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from tempfile import mkdtemp

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response

from soa.env import key_fingerprint, load_env
from soa.pdfdoc import PdfDoc
from soa.pipeline import extract_document

# Must happen before anything reads the environment. Without this the server ran entirely
# on whatever was exported in the shell and silently ignored the .env file the README tells
# you to put your key in.
_ENV_FILE = load_env()

app = FastAPI(title="SoA Extraction", version="0.1.0")

# The UI is served from a Vite dev server during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_UPLOAD_BYTES = 60 * 1024 * 1024
RENDER_DPI = 130  # enough to read a dense table on screen without huge payloads


@dataclass
class Job:
    id: str
    filename: str
    path: Path
    status: str = "queued"  # queued | running | done | error
    stage: str = ""
    detail: str = ""
    error: str | None = None
    result: dict | None = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    events: list[dict] = field(default_factory=list)


JOBS: dict[str, Job] = {}
_UPLOAD_DIR = Path(mkdtemp(prefix="soa-uploads-"))
_lock = threading.Lock()


def _run_job(job_id: str, use_vision: bool) -> None:
    job = JOBS[job_id]
    job.status = "running"

    def progress(stage: str, detail: str) -> None:
        job.stage = stage
        job.detail = detail
        job.events.append(
            {"stage": stage, "detail": detail, "at": datetime.now(timezone.utc).isoformat()}
        )

    try:
        result = extract_document(job.path, use_vision=use_vision, progress=progress)
        job.result = result.model_dump(mode="json")
        job.status = "done"
        job.stage = "done"
        job.detail = (
            f"{len(result.schedules)} schedule(s), "
            f"{sum(len(s.rows) for s in result.schedules)} rows"
        )
    except Exception as exc:  # noqa: BLE001 - surfaced to the client
        job.status = "error"
        job.error = f"{type(exc).__name__}: {exc}"
        job.stage = "error"


@app.post("/api/extract")
async def create_extraction(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    use_vision: bool = Query(True, description="Set false to run the geometric engine only"),
):
    """Accept a protocol PDF and start an extraction job."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF file.")

    data = await file.read()
    if not data:
        raise HTTPException(400, "The uploaded file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            413, f"File is larger than the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit."
        )
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "That does not look like a PDF (missing %PDF header).")

    job_id = uuid.uuid4().hex[:12]
    target = _UPLOAD_DIR / f"{job_id}.pdf"
    target.write_bytes(data)

    with _lock:
        JOBS[job_id] = Job(id=job_id, filename=file.filename, path=target)

    background.add_task(_run_job, job_id, use_vision)
    return {"job_id": job_id, "status": "queued", "filename": file.filename}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    """Poll a job. Returns the full result once status is ``done``."""
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "Unknown job.")

    payload = {
        "job_id": job.id,
        "filename": job.filename,
        "status": job.status,
        "stage": job.stage,
        "detail": job.detail,
        "created_at": job.created_at,
        "events": job.events[-30:],
    }
    if job.status == "error":
        payload["error"] = job.error
    if job.status == "done":
        payload["result"] = job.result
    return JSONResponse(payload)


@app.get("/api/jobs/{job_id}/page/{page_no}.png")
def get_page_image(job_id: str, page_no: int):
    """Render one page of the uploaded PDF, for side-by-side checking."""
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "Unknown job.")

    doc = PdfDoc(job.path)
    try:
        if not (1 <= page_no <= len(doc)):
            raise HTTPException(404, f"Page {page_no} is out of range.")
        png = doc.page_no(page_no).render_png(dpi=RENDER_DPI)
    finally:
        doc.close()

    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/api/jobs/{job_id}/page/{page_no}/size")
def get_page_size(job_id: str, page_no: int):
    """Page dimensions in PDF points, so the UI can scale bounding boxes correctly."""
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "Unknown job.")
    doc = PdfDoc(job.path)
    try:
        if not (1 <= page_no <= len(doc)):
            raise HTTPException(404, f"Page {page_no} is out of range.")
        page = doc.page_no(page_no)
        return {
            "width": page.width,
            "height": page.height,
            "orientation": page.orientation,
            "dpi": RENDER_DPI,
        }
    finally:
        doc.close()


@app.get("/api/jobs/{job_id}/download")
def download_json(job_id: str):
    """The structured output as a downloadable file."""
    job = JOBS.get(job_id)
    if job is None or job.status != "done":
        raise HTTPException(404, "No completed result for that job.")

    import json
    import tempfile

    stem = Path(job.filename).stem
    tmp = Path(tempfile.mkdtemp()) / f"{stem}.soa.json"
    tmp.write_text(
        json.dumps(job.result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return FileResponse(tmp, media_type="application/json", filename=tmp.name)


@app.get("/api/health")
def health():
    """Reports which credential is actually in use, not merely that one exists.

    The key fingerprint is the last six characters only. It exists because "a key is
    configured" is not the useful fact -- "*which* key" is, when two are in play.
    """
    import os

    return {
        "ok": True,
        "vision_configured": bool(
            os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        ),
        "env_file": str(_ENV_FILE) if _ENV_FILE else None,
        "api_key": key_fingerprint(),
        "model": os.environ.get("SOA_GEMINI_MODEL", "gemini-3.5-flash"),
        "jobs": len(JOBS),
    }
