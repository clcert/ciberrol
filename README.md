# Ciberrol

Aplicación para ejercicios de simulación de incidentes (juego de rol) con cuatro roles:
👔 Directivo (amarillo), 📢 Comunicacional (verde), ⚖️ Legal (rojo) y 🛠️ Técnico (azul).

Stack: Python (FastAPI) + PostgreSQL + Caddy, con Docker Compose. Todo el estado (actividades,
diapositivas, participantes, propuestas, votos, paso actual) se guarda en Postgres al instante.

## Levantar

```bash
cp .env.example .env        # ajustar PUBLIC_URL, clave de Postgres, dominio…
docker compose up -d --build
docker compose logs app | grep -A4 "CUENTA ADMIN"
```

La primera vez se crea la cuenta `admin` con una clave aleatoria que **solo aparece en los logs**.
Para generar otra: `docker compose exec app python -m ciberrol.reset_admin`.

**Importante:** define `PUBLIC_URL` en `.env` (p. ej. `http://192.168.1.50`) con la dirección que usarán
los teléfonos. Es la que se muestra en el QR. Si está vacía se usa la dirección con la que abriste el admin
(si fue `localhost`, los teléfonos no podrán entrar).

## Rutas

| Ruta | Quién | Qué hace |
|---|---|---|
| `/CODIGO` (p. ej. `/F4D2B`) | Participantes | URL corta: nombre + rol, y luego sigue el ejercicio desde el móvil. |
| `/` | Participantes | Formulario para escribir el código. |
| `/admin` | Admin | Lista y creación de actividades. |
| `/admin/a/<id>` | Admin | Panel de la actividad: controles, avance, diapositiva actual con resultados en vivo, participantes, línea de tiempo, exportar/importar. |
| `/admin/a/<id>/presentacion` | Admin (proyector) | QR al inicio; luego fase, pasos, temporizador, grilla 2x2 e indicadores. |
| `/admin/a/<id>/resultados` | Admin | Estadísticas por persona (y CSV). |

## Flujo de una actividad

1. El admin crea la actividad (obtiene un código corto) y sus diapositivas en las fases
   **Contener → Mitigar → Recuperar → Aprendizaje**. Cada diapositiva tiene un escenario y un indicador 0–100
   por rol, el número de alternativas (por defecto 3) y los tiempos para proponer y votar (por defecto 30 s).
2. La presentación muestra el **QR** y la URL corta; la gente se une con su nombre y rol.
3. **Comenzar ejercicio** → primera diapositiva. Cada diapositiva tiene 4 pasos:
   1. **Escenario**: se muestran los 4 textos (cada móvil ve el de su rol).
   2. **Propuestas**: por cada rol se eligen N personas (N = número de alternativas) siguiendo una
      permutación aleatoria por rol (nadie repite hasta que todos los de su rol hayan propuesto). Tienen el
      tiempo configurado para escribir; el texto se guarda mientras escriben y, al acabarse el tiempo, se toma lo
      que alcanzaron a escribir. Se cierra solo al vencer el plazo o cuando todos enviaron.
   3. **Votación** (el admin da el inicio): todas las personas de cada rol, menos quienes propusieron, votan
      una de las propuestas de su rol (A, B, C…). Un voto, sin cambios. Se cierra sola al vencer el plazo o
      cuando todos votaron.
   4. **Resultados**: gráfico con votos y porcentaje por alternativa en cada card.
4. **Finalizar ejercicio** → el admin ve por persona: cuántas veces sugirió, no sugirió (le tocó y no escribió),
   su alternativa ganó (empates cuentan), votó y no votó (podía y no lo hizo).

Otros controles: anterior/siguiente, *Cerrar ahora* (adelanta el fin del paso con tiempo), *Reiniciar
diapositiva* (borra propuestas y votos de esa diapositiva), *Mostrar/Ocultar QR* durante el ejercicio
(para quienes llegan tarde) y *Reabrir*.

## Exportar / importar

En la línea de tiempo: **Exportar diapositivas** descarga un JSON; **Importar** lo agrega a otra actividad
(o reemplaza sus diapositivas). Útil para reutilizar un escenario con varios grupos.

## Configuración (`.env`)

- `PUBLIC_URL`: dirección que aparece en el QR.
- `SITE_ADDRESS`: `:80` = HTTP en cualquier IP (red local). Con un dominio público Caddy obtiene HTTPS solo.
- `COOKIE_SECURE=true` si usas HTTPS.
- `POSTGRES_*`: credenciales de la base de datos.

Colores y emojis de los roles: `PROFILES` en `app/ciberrol/models.py`.
