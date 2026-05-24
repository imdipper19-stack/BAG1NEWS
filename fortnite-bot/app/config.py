from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import AliasChoices, Field


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
    # Closerouter). Set this on RU/Iran VPS where most western
    # services are blocked at the network level. Same format as
    # telegram_proxy_url.
    outbound_proxy_url: str = ""

    # AI provider (Closerouter / OpenAI-compatible)
    llm_api_url: str = Field(
        "https://api.closerouter.dev",
        validation_alias=AliasChoices("LLM_API_URL", "CLOSEROUTER_BASE_URL"),
    )
    llm_api_key: str = Field(
        "",
        validation_alias=AliasChoices("LLM_API_KEY", "CLOSEROUTER_API_KEY"),
    )
    llm_model: str = Field(
        "openai/gpt-5.5",
        validation_alias=AliasChoices("LLM_MODEL", "CLOSEROUTER_TEXT_MODEL"),
    )
    image_api_url: str = Field(
        "https://api.closerouter.dev",
        validation_alias=AliasChoices("IMAGE_API_URL", "CLOSEROUTER_BASE_URL"),
    )
    image_api_key: str = Field(
        "",
        validation_alias=AliasChoices(
            "IMAGE_API_KEY",
            "CLOSEROUTER_API_KEY",
            "LLM_API_KEY",
        ),
    )
    image_model: str = Field(
        "openai/gpt-image-2",
        validation_alias=AliasChoices("IMAGE_MODEL", "CLOSEROUTER_IMAGE_MODEL"),
    )
    image_size: str = "1536x864"

    # Optional Telegram custom emoji IDs. Leave empty to use normal Unicode emoji.
    custom_emoji_news_id: str = ""
    custom_emoji_shop_id: str = ""
    custom_emoji_chat_id: str = ""
    custom_emoji_alert_id: str = ""

    # Database
    database_url: str = "postgresql+asyncpg://fortnite:fortnite123@postgres:5432/fortnite_bot"
    postgres_user: str = "fortnite"
    postgres_password: str = "fortnite123"
    postgres_db: str = "fortnite_bot"

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # Shop
    shop_url: str = "https://bag1-v-bucks.shop/"

    # Reddit
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "FortniteNewsBot/1.0"

    # Publishing
    max_posts_per_day: int = 20
    min_score_to_publish: int = 70


settings = Settings()
