"""Local HTTP boundary for image uploads and analysis jobs."""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock
from typing import Literal
from uuid import uuid4

import numpy as np
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, model_validator

from ..composition import guess_composition
from ..config import Params
from ..pipeline import analyze
from .assets import build_assets

LOG = logging.getLogger(__name__)
Kind = Literal["swir", "truecolor", "wildfire"]


class ErrorResponse(BaseModel):
    code: str


class UploadInfo(BaseModel):
    id: str
    name: str
    width: int
    height: int
    bytes: int
    detected: Kind
    preview: str


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    swir: str | None = None
    truecolor: str | None = None
    wildfire: str | None = None
    max_side: Literal[1000, 1600, 2500] = 2500
    smoke_recovery: bool = True
    sensitivity: Literal["conservative", "balanced", "sensitive"] = "balanced"

    @model_validator(mode="after")
    def require_detection_image(self) -> AnalysisRequest:
        """Rejects reference-only requests before they enter the analysis queue.

        Raises:
            ValueError: If neither SWIR nor True Color is assigned.
        """
        if not self.swir and not self.truecolor:
            raise ValueError("A SWIR or True Color image is required for detection.")
        return self


class JobResponse(BaseModel):
    id: str
    status: Literal["queued", "running", "completed", "failed"]
    stage: str
    error: str | None = None
    result: dict | None = None


ERRORS = {
    400: {"model": ErrorResponse, "description": "Invalid or duplicate image assignments."},
    404: {"model": ErrorResponse, "description": "The upload, job, or file does not exist."},
    413: {"model": ErrorResponse, "description": "The image exceeds the byte or pixel limit."},
    415: {"model": ErrorResponse, "description": "The file is not a readable PNG or JPEG image."},
    422: {"model": ErrorResponse, "description": "Invalid analysis options."},
}


class ImageService:
    """Owns a single-worker local queue and isolated output directories."""

    def __init__(self, directory: Path, max_bytes: int) -> None:
        self.directory = directory
        self.max_bytes = max_bytes
        self.uploads: dict[str, tuple[UploadInfo, Path]] = {}
        self.jobs: dict[str, JobResponse] = {}
        self.lock = Lock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="firewatch")

    def _update(self, job_id: str, **values: object) -> None:
        with self.lock:
            self.jobs[job_id] = self.jobs[job_id].model_copy(update=values)

    def run(self, job_id: str, options: AnalysisRequest, paths: list[Path | None]) -> None:
        """Runs CPU work outside the HTTP event loop and records failures."""
        self._update(job_id, status="running", stage="loading")
        try:
            params = Params()
            params.burn.smoke_recovery = options.smoke_recovery
            if options.sensitivity == "conservative":
                params.burn.hyst_high, params.burn.hyst_low = 0.58, 0.38
            elif options.sensitivity == "sensitive":
                params.burn.hyst_high, params.burn.hyst_low = 0.44, 0.25
            analysis = analyze(
                swir_path=paths[0], truecolor_path=paths[1], reference_path=paths[2],
                params=params, max_side=options.max_side, auto_assign=False,
                on_progress=lambda stage: self._update(job_id, stage=stage),
            )
            self._update(job_id, stage="exports")
            directory = self.directory / job_id
            directory.mkdir()
            result = build_assets(analysis, directory)
            result["options"] = options.model_dump(exclude={"swir", "truecolor", "wildfire"})
            result["inputs"] = {
                kind: self.uploads[upload_id][0].model_dump()
                for kind in ("swir", "truecolor", "wildfire")
                if (upload_id := getattr(options, kind)) is not None
            }
            self._update(job_id, status="completed", stage="completed", result=result)
        except Exception:
            LOG.exception("Analysis %s failed", job_id)
            self._update(job_id, status="failed", stage="failed", error="analysis_failed")


def _inspect_image(path: Path, preview: Path) -> tuple[int, int, Kind]:
    with Image.open(path) as image:
        if image.format not in ("PNG", "JPEG"):
            raise HTTPException(415, "invalid_image")
        width, height = image.size
        if width * height > 100_000_000:
            raise HTTPException(413, "too_many_pixels")
        image.thumbnail((640, 640))
        image = image.convert("RGB")
        kind, _ = guess_composition(np.asarray(image, dtype=np.float32))
        image.save(preview, format="PNG")
        return width, height, kind


def create_app(data_dir: Path | None = None, max_upload_bytes: int = 100 * 1024 * 1024) -> FastAPI:
    """Creates an image-only API with independent storage for each app instance."""
    directory = data_dir or Path(os.environ.get("FIREWATCH_DATA_DIR", "out/web"))

    @asynccontextmanager
    async def lifespan(api: FastAPI):
        directory.mkdir(parents=True, exist_ok=True)
        service = ImageService(directory, max_upload_bytes)
        api.state.service = service
        yield
        await run_in_threadpool(service.executor.shutdown, wait=True)

    api = FastAPI(title="Firewatch Image API", version="0.1.0", lifespan=lifespan)
    origins = os.environ.get("FIREWATCH_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    api.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

    @api.exception_handler(HTTPException)
    async def http_error(_request: Request, error: HTTPException) -> JSONResponse:
        return JSONResponse({"code": error.detail}, status_code=error.status_code)

    @api.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, _error: RequestValidationError) -> JSONResponse:
        return JSONResponse({"code": "invalid_request"}, status_code=422)

    @api.get("/api/health", summary="Check API availability")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @api.post("/api/uploads", response_model=UploadInfo, status_code=201,
              summary="Upload and identify a PNG or JPEG", responses=ERRORS)
    async def upload(request: Request, file: UploadFile = File(...)) -> UploadInfo:
        """Accepts up to 100 MiB and 100 million pixels per image.

        Raises:
            HTTPException: If the file exceeds a limit or cannot be decoded.
        """
        service: ImageService = request.app.state.service
        upload_id = uuid4().hex
        target = directory / f"{upload_id}.input"
        preview = directory / f"{upload_id}.png"
        total = 0
        try:
            with target.open("wb") as output:
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > service.max_bytes:
                        raise HTTPException(413, "file_too_large")
                    output.write(chunk)
            width, height, kind = await run_in_threadpool(_inspect_image, target, preview)
        except (UnidentifiedImageError, OSError, ValueError) as error:
            target.unlink(missing_ok=True)
            preview.unlink(missing_ok=True)
            raise HTTPException(415, "invalid_image") from error
        except HTTPException:
            target.unlink(missing_ok=True)
            preview.unlink(missing_ok=True)
            raise
        finally:
            await file.close()
        info = UploadInfo(id=upload_id, name=Path(file.filename or "image").name[:160],
                          width=width, height=height, bytes=total, detected=kind,
                          preview=f"/api/uploads/{upload_id}/preview")
        with service.lock:
            service.uploads[upload_id] = (info, target)
        return info

    @api.get("/api/uploads/{upload_id}/preview", summary="Read a reduced image preview", responses={404: ERRORS[404]})
    def preview(request: Request, upload_id: str) -> FileResponse:
        service: ImageService = request.app.state.service
        if upload_id not in service.uploads:
            raise HTTPException(404, "upload_not_found")
        return FileResponse(directory / f"{upload_id}.png", media_type="image/png")

    @api.post("/api/analyses", response_model=JobResponse, status_code=202,
              summary="Queue image analysis", responses=ERRORS)
    def start(request: Request, options: AnalysisRequest) -> JobResponse:
        """Queues detection from SWIR or True Color, with optional validation reference.

        Raises:
            HTTPException: If an upload is missing or assigned more than once.
        """
        service: ImageService = request.app.state.service
        ids = [options.swir, options.truecolor, options.wildfire]
        selected = [value for value in ids if value is not None]
        if len(set(selected)) != len(selected):
            raise HTTPException(400, "duplicate_image")
        with service.lock:
            if any(value not in service.uploads for value in selected):
                raise HTTPException(404, "upload_not_found")
            paths = [service.uploads[value][1] if value else None for value in ids]
            job = JobResponse(id=uuid4().hex, status="queued", stage="queued")
            service.jobs[job.id] = job
        service.executor.submit(service.run, job.id, options, paths)
        return job

    @api.get("/api/analyses/{job_id}", response_model=JobResponse,
             summary="Read analysis status and results", responses={404: ERRORS[404]})
    def status(request: Request, job_id: str) -> JobResponse:
        service: ImageService = request.app.state.service
        with service.lock:
            job = service.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "job_not_found")
        return job

    @api.get("/api/analyses/{job_id}/files/{filename}", summary="Read a result image or pixel report",
             responses={404: ERRORS[404]})
    def asset(request: Request, job_id: str, filename: str, download: bool = False) -> FileResponse:
        service: ImageService = request.app.state.service
        with service.lock:
            job = service.jobs.get(job_id)
        if job is None or job.result is None or filename not in job.result["files"]:
            raise HTTPException(404, "file_not_found")
        return FileResponse(directory / job_id / filename, filename=filename if download else None,
                            headers={"X-Content-Type-Options": "nosniff"})

    return api


app = create_app()
