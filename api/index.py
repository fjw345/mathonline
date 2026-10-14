"""Expose the FastAPI application to Vercel's Python runtime."""

from backend.main import app

__all__ = ["app"]
