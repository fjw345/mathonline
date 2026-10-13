import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
MODE = os.getenv("APP_MODE", "local")
if MODE not in ("local", "supabase"):
    raise RuntimeError("APP_MODE must be local or supabase")
ORIGINS = [
    v.strip().rstrip("/")
    for v in os.getenv(
        "APP_ORIGIN",
        "http://127.0.0.1:8011,http://localhost:8011,http://127.0.0.1:5173,http://localhost:5173",
    ).split(",")
]
DB_PATH = Path(os.getenv("LOCAL_DATABASE", str(ROOT / "data/mathonline.sqlite")))
if not DB_PATH.is_absolute():
    DB_PATH = ROOT / DB_PATH
PREVIEW = (
    MODE == "local" and os.getenv("LOCAL_PREVIEW_DRAFTS", "false").lower() == "true"
)
SB_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SB_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
SB_ANON = os.getenv("SUPABASE_ANON_KEY", "")
BUCKET = os.getenv("SUPABASE_BUCKET", "mathonline")
if MODE == "supabase" and not all((SB_URL, SB_KEY, SB_ANON)):
    raise RuntimeError("Supabase mode requires URL, service role key and anon key")
