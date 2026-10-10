from pathlib import Path
from typing import Literal
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    research_mode: str = "demo"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = ""
    llm_provider: Literal["openai_compatible", "deepseek"] = "openai_compatible"
    chat_mode: Literal["auto", "demo", "live"] = "auto"
    llm_max_tokens: int = Field(default=8192, ge=1, le=393216)
    llm_timeout_seconds: int = Field(default=120, ge=1, le=600)
    deepseek_thinking: Literal["enabled", "disabled"] = "disabled"
    deepseek_reasoning_effort: Literal["low", "high", "max"] = "high"
    tavily_api_key: str = ""
    data_dir: str = "./data"
    max_researchers: int = 3
    max_model_calls: int = Field(default=80, ge=5, le=200)
    report_reserved_calls: int = Field(default=4, ge=1, le=10)
    max_search_calls: int = Field(default=24, ge=1, le=60)
    max_gap_rounds: int = 1
    run_timeout_seconds: int = 600
    mcp_enabled: bool = False
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = ""
    langfuse_redact: str = ""
    synthesis_context_budget: int = Field(default=24000, ge=400, le=200000)
    synthesis_quotes_per_cell: int = Field(default=2, ge=1, le=4)
    # Estimate-only price table for live eval. Not a billing statement.
    eval_price_version: str = "2026-10-10-v1"
    eval_deepseek_prompt_usd_per_1m: float = Field(default=0.27, ge=0)
    eval_deepseek_completion_usd_per_1m: float = Field(default=1.10, ge=0)
    eval_deepseek_reasoning_usd_per_1m: float = Field(default=1.10, ge=0)
    eval_tavily_search_usd: float = Field(default=0.008, ge=0)
    eval_max_tokens_per_task: int = Field(default=0, ge=0, le=2000000)

    @property
    def tracing_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key and self.langfuse_host)

    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir)
        return p if p.is_absolute() else ROOT / p

    @property
    def live_ready(self) -> bool:
        return bool(self.llm_api_key and self.llm_model and self.tavily_api_key)

    @property
    def effective_chat_mode(self) -> str:
        if self.chat_mode != "auto":
            return self.chat_mode
        return "live" if self.llm_api_key and self.llm_model else self.research_mode


settings = Settings()
