"""FastAPI application factory and lifespan."""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from mtfwbuilder.config import load_settings
from mtfwbuilder.database import init_db
from mtfwbuilder.services.device_registry import DeviceRegistry


def setup_logging(log_level: str, log_json: bool) -> None:
    """Configure application logging."""
    level = getattr(logging, log_level.upper(), logging.INFO)
    fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    if log_json:
        # Structured JSON logging for production
        import json

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

    # Load device registry
    registry = DeviceRegistry(settings.devices_file)
    app.state.device_registry = registry
    logger.info(f"Loaded {registry.count} device variants")

    # Initialize build system
    from mtfwbuilder.services.build_service import init_build_system

    init_build_system(settings)
    logger.info("Build system initialized (serialized, 1 concurrent build)")

    yield

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
    from slowapi import _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded
    from mtfwbuilder.rate_limit import limiter

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # Routers
    from mtfwbuilder.routers.config_generator import router as config_router
    from mtfwbuilder.routers.firmware_builder import router as firmware_router
    from mtfwbuilder.routers.admin import router as admin_router
    from mtfwbuilder.routers.pages import router as pages_router

    app.include_router(config_router)
    app.include_router(firmware_router)
    app.include_router(admin_router)
    app.include_router(pages_router)

    # Static files mount AFTER routers — Starlette matches routes in order,
    # and /static must not shadow API routes, but url_for('static') still works
    if os.path.isdir(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    return app


# Application instance for uvicorn
app = create_app()
