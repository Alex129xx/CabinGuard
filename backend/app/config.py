from __future__ import annotations

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")

    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_use_env_proxy: bool = False
    amap_web_service_key: str | None = None
    azure_speech_key: str | None = None
    azure_speech_region: str | None = None
    azure_speech_endpoint: str | None = None
    azure_speech_enabled: bool = False

    @property
    def llm_enabled(self) -> bool:
        return bool(self.deepseek_api_key)

    @property
    def amap_enabled(self) -> bool:
        return bool(self.amap_web_service_key)

    @property
    def azure_enabled(self) -> bool:
        return bool(self.azure_speech_enabled and self.azure_speech_key and (self.azure_speech_region or self.azure_speech_endpoint))


settings = Settings()
