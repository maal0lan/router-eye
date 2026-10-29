from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = "RouterScope"
    database_url: str = "sqlite:///./routerscope.db"
    admin_username: str = "admin"
    admin_password: str = "change-this-before-deploying"
    encryption_key: str = ""
    session_secret: str = "change-this-session-secret-before-deploying"
    cookie_secure: bool = False
    cors_origins: str = "http://localhost:5173"
    poll_minutes: int = 15
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()
