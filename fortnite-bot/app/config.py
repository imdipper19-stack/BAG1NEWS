from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Telegram
    telegram_bot_token: str
    telegram_channel_id: str = "@FortnitebucksShop"
    # Telegram numeric user ID of the channel admin who will receive
    # approval requests in DM. Get it from @userinfobot.
    telegram_admin_user_id: int = 0
    # Master switch — when True, every post is sent to the admin for
    # approve/reject before being published to the channel.
    require_admin_approval: bool = True
    # Optional HTTP/HTTPS proxy for the Telegram Bot API.
    # Use when the VPS region blocks Telegram (e.g. RU). Format:
    #   http://user:pass@host:port  or  http://host:port
    telegram_proxy_url: str = ""

    # Optional HTTP proxy used by every outbound HTTP call from the bot
    # (Fortnite-API, fortnite.com, YouTube RSS, Reddit, nitter, fortnite.gg,
    # Replicate, wellflow.dev). Set this on RU/Iran VPS where most western
    # services are blocked at the network level. Same format as
    # telegram_proxy_url.
    outbound_proxy_url: str = ""

    # LLM
    llm_api_url: str = "https://api.wellflow.dev"
    llm_api_key: str
    llm_model: str = "gpt-5.5"

    # Replicate
    replicate_api_token: str

    # Database
    database_url: str = "postgresql+asyncpg://fortnite:fortnite123@postgres:5432/fortnite_bot"
    postgres_user: str = "fortnite"
    postgres_password: str = "fortnite123"
    postgres_db: str = "fortnite_bot"

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # Shop
    shop_url: str = "https://bag1v-bucks.shop/"

    # Reddit
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "FortniteNewsBot/1.0"

    # Publishing
    max_posts_per_day: int = 20
    min_score_to_publish: int = 70


settings = Settings()
