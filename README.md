# Agente de verificación de identidad (KYC)

[![tests](https://github.com/acromaticodiego/auditable-kyc-agent/actions/workflows/tests.yml/badge.svg)](https://github.com/acromaticodiego/auditable-kyc-agent/actions/workflows/tests.yml)

Un usuario sube las dos caras de su cédula colombiana y una selfie. Código
determinista mide **28 señales** —OCR del anverso, la MRZ con sus dígitos de
control, los cotejos entre ambos, la calidad de la captura y la similitud
facial— y un agente decide **aprobar**, **rechazar**, **pedir otra foto** o
**escalar a un humano**.

**El modelo nunca ve las imágenes**: recibe esas 28 señales como texto, en una
sola llamada. Y no devuelve un párrafo: cada fundamento cita la señal concreta
y el valor que le atribuye, y el sistema **comprueba cada cita** contra el
valor medido antes de registrarla.

| Conjunto reservado — 14 casos, medidos una sola vez | |
|---|---|
| **Agente** | **13/14** |
| Línea base de reglas fijas, los mismos 14 casos | 11/14 |
| Citas verificadas una a una | **49/49** |

*Catorce casos sintéticos de una sola identidad: muestra pequeña, y lo que
sostiene es la comparación.*

**Stack:** Python · FastAPI · PostgreSQL · Docker · Tesseract · OpenCV ·
InsightFace (SCRFD + ArcFace) sobre ONNX Runtime · Gemini con salida
estructurada. 393 tests en CI.

Se levanta con `docker compose up -d` y la pantalla queda en
<http://localhost:8000/>. **El resto de este README es la evidencia detrás de
esos tres números**: cómo se midieron, qué falló por el camino y qué no se
puede afirmar con ellos.
