from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    api_base_url: str = "https://<your-api-host>"
    data_root: Path = Path("data")
    file_ttl_days: int = 7
    storage_backend: str = "local"
    enable_shortlink: bool = False
    oss_endpoint: str = "oss-cn-hangzhou.aliyuncs.com"
    oss_bucket: str = "private-wosai-statics"
    oss_prefix: str = "quanlaidian-quote"
    oss_public_base_url: str = "https://private-resource.shouqianba.com"
    oss_access_key_id: str = ""
    oss_access_key_secret: str = ""
    log_level: str = "INFO"

    model_config = {"env_prefix": "QUOTE_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
