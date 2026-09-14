FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/srv

WORKDIR /srv

# fonts-dejavu-core da una sans y una monoespaciada de verdad.  Sin fuentes
# TrueType, Pillow cae a un mapa de bits diminuto y las cedulas sinteticas
# saldrian con un texto que ningun OCR podria leer, con lo que el conjunto
# de evaluacion no mediria el OCR sino el renderizador.
RUN apt-get update     && apt-get install -y --no-install-recommends fonts-dejavu-core     && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY tests ./tests
COPY pytest.ini ./

# Sin privilegios. Los volumenes montados en desarrollo se leen igual.
RUN useradd --create-home --uid 1000 kyc && chown -R kyc:kyc /srv
USER kyc

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
