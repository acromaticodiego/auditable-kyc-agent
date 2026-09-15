FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/srv

WORKDIR /srv

# fonts-dejavu-core da una sans y una monoespaciada de verdad.  Sin fuentes
# TrueType, Pillow cae a un mapa de bits diminuto y las cedulas sinteticas
# saldrian con un texto que ningun OCR podria leer, con lo que el conjunto
# de evaluacion no mediria el OCR sino el renderizador.
RUN apt-get update     && apt-get install -y --no-install-recommends         fonts-dejavu-core         tesseract-ocr         tesseract-ocr-spa     && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
# insightface declara `opencv-python` -- la build CON interfaz grafica --
# como dependencia, y esa gana el import de `cv2` sobre la headless.  En una
# imagen slim eso revienta al importar con `libGL.so.1: cannot open shared
# object file`, porque aqui no hay ni pantalla ni librerias graficas.
#
# La salida no es instalar libGL en un servidor sin pantalla, sino dejar una
# sola build de OpenCV.  Se desinstalan las dos y se reinstala la headless:
# ambas escriben en el mismo directorio `cv2/`, asi que quitar solo una
# dejaria a la otra a medias.
RUN pip install --no-cache-dir -r requirements.txt     && pip uninstall -y opencv-python opencv-python-headless     && pip install --no-cache-dir "opencv-python-headless>=4.9,<6.0"     && python -c "import cv2, insightface; print('cv2', cv2.__version__)"

COPY app ./app
COPY tests ./tests
COPY pytest.ini ./

# Sin privilegios. Los volumenes montados en desarrollo se leen igual.
RUN useradd --create-home --uid 1000 kyc && chown -R kyc:kyc /srv
USER kyc

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
