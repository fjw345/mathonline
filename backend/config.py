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
RESOURCE_DOWNLOAD_URL = os.getenv("RESOURCE_DOWNLOAD_URL", "").strip()
RESOURCE_EXTRACTION_CODE = os.getenv("RESOURCE_EXTRACTION_CODE", "").strip()
if MODE == "supabase" and not all((SB_URL, SB_KEY, SB_ANON)):
    raise RuntimeError("Supabase mode requires URL, service role key and anon key")

# 模型判分配置：未配置时 AI 判分降级为待自检，不阻断提交。
AI_BASE_URL = os.getenv("AI_BASE_URL", "").rstrip("/")
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "")
# 可选：考试预测蓝图单独用一个快速模型，避免强推理模型在长提示上超时或截断。
AI_BLUEPRINT_MODEL = os.getenv("AI_BLUEPRINT_MODEL", "").strip() or AI_MODEL
AI_TIMEOUT = float(os.getenv("AI_TIMEOUT", "30"))
# 可选：思考型模型（如 Ollama qwen3）可用 none 关闭思考，避免推理占满输出预算。
# 留空表示不发送该字段，兼容不识别它的服务商。
AI_REASONING_EFFORT = os.getenv("AI_REASONING_EFFORT", "").strip()
AI_ENABLED = bool(AI_BASE_URL and AI_API_KEY and AI_MODEL)
