"""Provider catalog constants shared by configuration and private persistence."""

MAX_BACKUP_PROVIDERS = 12
LOCAL_AI_SLOT = MAX_BACKUP_PROVIDERS + 1
XKIRO_BASE_URL = "https://api.xkiro.com/v1"
XKIRO_MODELS = (
    ("qwen/qwen3.8-max:free", "xKiro Qwen3.8 Max Free"),
    ("anthropic/claude-sonnet-5", "xKiro Sonnet 5"),
    ("openai/gpt-6.1-sol", "xKiro GPT-6.1 Sol"),
    ("anthropic/claude-opus-5.5", "xKiro Opus 5.5"),
)
XKIRO_DEFAULTS_VERSION = "1"
