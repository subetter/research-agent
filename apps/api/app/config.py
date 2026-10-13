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
    embedding_provider: Literal["local", "siliconflow", "openai_compatible"] = "local"
    embedding_model: str = "local-hash-v1"
    embedding_api_key: str = ""
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_dim: int = Field(default=64, ge=8, le=4096)
    siliconflow_api_key: str = ""
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    rerank_provider: Literal["auto", "local", "siliconflow"] = "auto"
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    embedding_timeout_seconds: int = Field(default=45, ge=5, le=180)
    rerank_timeout_seconds: int = Field(default=45, ge=5, le=180)
    embedding_batch_size: int = Field(default=32, ge=1, le=32)
    embedding_max_retries: int = Field(default=3, ge=0, le=8)
    index_version: str = "v1"
    fts_recall: int = Field(default=20, ge=1, le=50)
    vector_recall: int = Field(default=20, ge=1, le=50)
    rerank_top_k: int = Field(default=8, ge=6, le=10)
    mcp_enabled: bool = False

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
