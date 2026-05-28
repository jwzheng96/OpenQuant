"""OpenQuant Web backend — FastAPI app.

Reuses the main project's `open_quant` package directly (shared venv).
Designed to be runnable from the project root:

    uvicorn web.backend.app.main:app --reload --port 8000
"""
