from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://kyc:kyc@localhost:5434/kyc"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    # Vueltas maximas del bucle del agente.  El limite no es una precaucion
    # teorica: cada vuelta gasta una peticion del cupo diario gratuito, asi
    # que un agente que se enrede en un caso raro puede quemar el cupo de
    # toda la tanda de evaluacion.  Ver docs/adr/0001.
    agent_max_turns: int = 3


settings = Settings()
