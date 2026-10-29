from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    database_url: str = "postgresql://chessmind:chessmind@localhost:5433/chessmind"
    redis_url: str = "redis://localhost:6379/0"
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    llm_timeout: float = 40.0
    games_cap: int = 300
    # Games per job kept in the RQ queue at once; match the worker count so one user alone
    # still keeps every worker busy.
    analysis_workers: int = 8
    rate_limit: bool = True  # off only for load tests, which come from one IP
    engine_path: str = "stockfish"
    engine_nodes: int = 100_000
    # Two-pass analysis (0 = off): every position at quick_nodes first, then moves that
    # drop >= recheck_margin_pts win-% points are re-checked at engine_nodes.
    engine_quick_nodes: int = 10_000
    recheck_margin_pts: float = 5.0
    lichess_url: str = "https://lichess.org"
    # Lichess answers 404 to requests without a descriptive User-Agent.
    user_agent: str = "ChessMind-AI (github.com/AmirKhan024/ChessMind-AI)"

    @property
    def sqlalchemy_url(self) -> str:
        return self.database_url.replace("postgresql://", "postgresql+psycopg://", 1)


settings = Settings()
