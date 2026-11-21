"""FastAPI application factory and lifespan."""

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager

import aiosqlite
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slowapi.errors import RateLimitExceeded

from mtfwbuilder.config import load_settings
from mtfwbuilder.database import fail_unended_builds, init_db
from mtfwbuilder.htmx import validation_error_response
from mtfwbuilder.rate_limit import configure_rate_limits, limiter
from mtfwbuilder.routers.admin import rate_limit_response
from mtfwbuilder.routers.admin import router as admin_router
from mtfwbuilder.routers.config_generator import router as config_router
from mtfwbuilder.routers.firmware import router as firmware_versions_router
from mtfwbuilder.routers.firmware_builder import router as firmware_router
from mtfwbuilder.routers.fleets import router as fleets_router
from mtfwbuilder.routers.pages import router as pages_router
from mtfwbuilder.routers.profiles import router as profiles_router
from mtfwbuilder.routers.variants import router as variants_router
from mtfwbuilder.services.build_service import BuildQueue
from mtfwbuilder.services.cleanup_service import cleanup_every_interval, scrub_sources_after_restart
from mtfwbuilder.services.firmware_catalog import FirmwareCatalog
from mtfwbuilder.services.firmware_updater import (
    FirmwareInstalls,
    import_old_firmware_source,
    remove_install_staging,
    remove_unrecorded_sources,
)
from mtfwbuilder.services.fleet_runs import FleetRuns


def setup_logging(log_level: str, log_json: bool) -> None:
    """Configure application logging."""
    level = getattr(logging, log_level.upper(), logging.INFO)
    fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    if log_json:
        # Structured JSON logging for production
        class JsonFormatter(logging.Formatter):
            def format(self, record):
                return json.dumps(
                    {
                        "ts": self.formatTime(record),
                        "level": record.levelname,
                        "logger": record.name,
                        "msg": record.getMessage(),
                    }
                )

        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logging.basicConfig(level=level, handlers=[handler])
    else:
        logging.basicConfig(level=level, format=fmt)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown."""
    settings = load_settings()
    app.state.settings = settings

    # Logging
    setup_logging(settings.log_level, settings.log_json)
    logger = logging.getLogger("mtfwbuilder")
    logger.info("Starting MTFWBuilder v2.0.0")

    # Ensure temp directory exists
    os.makedirs(settings.temp_dir, exist_ok=True)

    # Initialize database
    await init_db(settings)
    logger.info(f"Database initialized at {settings.database_path}")

    # The build queue is in memory, so a build that had not ended when the process stopped can never end.
    async with aiosqlite.connect(settings.database_path) as db:
        failed = await fail_unended_builds(db, "Server restarted")
    if failed:
        logger.warning(f"Marked {failed} builds failed after a restart")
    catalog = FirmwareCatalog()
    await asyncio.to_thread(import_old_firmware_source, settings, catalog.resolve)
    remove_install_staging(settings.firmware_dir)
    await remove_unrecorded_sources(settings)
    scrub_sources_after_restart(settings.firmware_dir)

    build_queue = BuildQueue(settings)
    build_queue.start()
    app.state.build_queue = build_queue
    app.state.fleet_runs = FleetRuns(settings, build_queue)
    app.state.firmware_catalog = catalog
    app.state.firmware_installs = FirmwareInstalls(settings, build_queue, resolver=catalog.resolve)
    logger.info("Build queue started")
    cleanup_task = asyncio.create_task(cleanup_every_interval(settings, build_queue.unended_build_ids))
    configure_rate_limits(settings)

    yield

    cleanup_task.cancel()
    await asyncio.gather(cleanup_task, return_exceptions=True)
    # A fleet run that waits for a build must stop before the build queue cancels that build.
    await app.state.fleet_runs.stop()
    await build_queue.stop()
    logger.info("Shutting down MTFWBuilder")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="MTFWBuilder",
        description="Meshtastic firmware configuration generator and builder",
        version="2.0.0",
        lifespan=lifespan,
    )

    # Static files and templates — MUST be set up before routers that use templates
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    static_dir = os.path.join(base_dir, "static")
    template_dir = os.path.join(base_dir, "templates")

    app.state.templates = Jinja2Templates(directory=template_dir)

    # Rate limiting
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_response)
    app.add_exception_handler(RequestValidationError, validation_error_response)

    # Routers
    app.include_router(config_router)
    app.include_router(firmware_router)
    app.include_router(firmware_versions_router)
    app.include_router(variants_router)
    app.include_router(profiles_router)
    app.include_router(fleets_router)
    app.include_router(admin_router)
    app.include_router(pages_router)

    @app.get("/healthz")
    async def healthz() -> dict:
        """Return a fixed status, so the Docker healthcheck sees that the process serves requests."""
        return {"status": "ok"}

    # Static files mount AFTER routers — Starlette matches routes in order,
    # and /static must not shadow API routes, but url_for('static') still works
    if os.path.isdir(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    return app


# Application instance for uvicorn
app = create_app()
