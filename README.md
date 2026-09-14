# Agente de verificación de identidad (KYC)

Un usuario sube la foto de su cédula y una selfie. Un agente de IA decide
**aprobar**, **rechazar**, **escalar a revisión humana** o **solicitar un
reenvío**, razonando sobre varias señales a la vez y explicando la decisión
con fundamentos que citan la señal concreta que los sostiene.

> **Estado: en construcción.** Ahora mismo solo está el esqueleto: API,
> base de datos y sonda de salud. Lo que sigue no describe todavía nada
> que funcione, sino hacia dónde va.

## La idea

El protagonista es el agente, no la visión por computador. El OCR y la
similitud facial son dos fuentes de señal entre varias; el valor está en
cómo se combinan cuando no coinciden, y en que la explicación resultante
se pueda **auditar en lugar de leer y confiar**.

De ahí la restricción de diseño que atraviesa todo el proyecto: el agente
no devuelve un párrafo. Devuelve una decisión estructurada cuyos
fundamentos citan cada uno un identificador de señal y el valor que le
atribuyen. El sistema comprueba después cada cita contra el valor real.
Una explicación que cite una señal inexistente, o que le atribuya un valor
que no tenía, queda registrada como cita inválida.

## Cómo levantarlo

```powershell
Copy-Item .env.example .env
docker compose up -d --build
```

La API queda en <http://localhost:8000/docs> y Postgres en el puerto 5434
del anfitrión (5432 y 5433 ya los ocupa otro proyecto de esta maquina).

```powershell
Invoke-RestMethod http://localhost:8000/health
docker compose exec api pytest -q
```

## Decisiones de diseño

Las decisiones que costaron discusión están en [docs/adr/](docs/adr/), con
lo que se descartó y por qué.

- [ADR-0001](docs/adr/0001-presupuesto-de-peticiones-del-agente.md) — por
  qué las señales baratas se precalculan y solo las caras son herramientas
  del agente.
- [ADR-0002](docs/adr/0002-explicacion-auditable.md) — por qué la
  explicación cita señales con su valor y esas citas se verifican, en vez
  de ser prosa que hay que creerse.

## Métricas

El proyecto se juzga por dos números, no por la demo:

1. **Fidelidad de las citas.** Proporción de decisiones cuya explicación
   cita alguna señal inexistente o con el valor alterado.
2. **El agente frente a una línea base de reglas fijas.** Un árbol de
   decisión con umbrales duros, medido sobre el mismo conjunto reservado,
   para responder si el agente aporta algo o solo lo cuenta mejor.

Los umbrales y el prompt se ajustan sobre una mitad del conjunto; la otra
mitad no se toca hasta la medición final. Cualquier cifra publicada aquí
irá acompañada del tamaño de muestra y de si el corte se eligió sobre esos
mismos datos.

## Limitaciones

Esta sección crecerá conforme haya resultados que la llenen. Hoy:

- Un solo tipo de documento (cédula colombiana). No hay nada que sugiera
  que generalice a otros formatos.
- El conjunto de evaluación es mayoritariamente sintético. Los documentos
  reales usados para calibrar el OCR no se publican.
- El cupo gratuito de la API del modelo limita el tamaño de las tandas de
  evaluación; ver ADR-0001.
