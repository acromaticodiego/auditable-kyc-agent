from sqlalchemy import create_engine

from app.config import settings

# pool_pre_ping descarta conexiones muertas antes de usarlas: sin esto, la
# primera peticion despues de reiniciar el contenedor de la base de datos
# falla con una conexion cerrada en vez de reconectar.
engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
