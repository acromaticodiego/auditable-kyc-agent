from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://kyc:kyc@localhost:5434/kyc"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash"

    # No hay ajuste de "vueltas maximas" porque hoy no hay vueltas: el agente
    # hace UNA llamada por decision.  Todas las senales se calculan antes y
    # se le entregan en el prompt, asi que no tiene ninguna herramienta que
    # invocar ni motivo para pedir una segunda vuelta.  Existia el ajuste y
    # no lo leia nadie; un parametro que no hace nada acaba puesto a 5 por
    # alguien que espera que signifique algo.  Cuando el agente tenga
    # herramientas de verdad -- la similitud facial es la candidata, porque
    # es la unica senal cara -- el limite vuelve, y entonces con un test.


settings = Settings()
