from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False)

    symbol: str = "btcusdt"
    redis_url: str = "redis://localhost:6379/0"

    binance_futures_public_ws: str = "wss://fstream.binance.com/public"
    binance_futures_market_ws: str = "wss://fstream.binance.com/market"
    binance_futures_rest: str = "https://fapi.binance.com"

    binance_spot_ws: str = "wss://stream.binance.com:9443"
    binance_spot_rest: str = "https://api.binance.com"

    bybit_linear_ws: str = "wss://stream.bybit.com/v5/public/linear"

    api_host: str = "0.0.0.0"
    api_port: int = 8000


@lru_cache
def get_settings() -> Settings:
    return Settings()
