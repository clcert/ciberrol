# Ciberrol

Aplicación para ejercicios de simulación de incidentes (juego de rol) con cuatro roles:
👔 Directivo (amarillo), 📢 Comunicacional (verde), ⚖️ Legal (rojo) y 🛠️ Técnico (azul).

Stack: Python (FastAPI) + PostgreSQL + Caddy, con Docker Compose. Todo el estado (actividades,
diapositivas, participantes, propuestas, votos, paso actual) se guarda en Postgres al instante.

## Levantar

La forma corta es el script `up.sh`, que reconstruye la imagen, levanta todo, espera a que la app responda y
muestra la versión desplegada (y la clave del admin si se acaba de crear):

```bash
./up.sh              # desarrollo / servidor propio (con el Caddy incluido)
./up.sh prod         # producción con caddy-docker-proxy (docker-compose.prod.yml)
./up.sh prod --pull  # igual, pero antes hace git pull
```

O a mano:

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

Los textos de las diapositivas (contexto, escenarios y preguntas) admiten markdown básico: `**negrita**`,
`*cursiva*`, `` `código` ``, listas (`- ` o `1. `), títulos (`# `) y enlaces `[texto](https://…)`.

1. El admin crea la actividad (obtiene un código corto) y sus diapositivas en las fases
   **Preparar → Contener → Mitigar → Recuperar → Evaluar**. Cada diapositiva tiene un contexto general y, por rol, un escenario, una pregunta
   (se muestra bajo el escenario) y un indicador 0–100, además del número de alternativas (por defecto 3).
   Los **tiempos** son de la actividad, no de cada diapositiva: discusión (por defecto 120 s; 0 = sin contador),
   propuestas y votación (por defecto 60 s). Se fijan al crearla y se cambian en el panel (*Tiempos de cada paso*).
2. La presentación muestra el **QR** y la URL corta; la gente se une solo con su nombre (no se permiten
   nombres repetidos en la misma actividad, sin distinguir mayúsculas).
3. **Asignar roles** (panel, antes de comenzar): se recorre una permutación aleatoria de los 4 roles asignando
   uno por persona; al acabarse se baraja otra, así los roles quedan equilibrados. *Reasignar roles a todos*
   rehace la asignación; *Asignar rol a personas nuevas* solo a quienes llegaron después. Al comenzar, quien no
   tenga rol recibe uno automáticamente, igual que quien se une con el ejercicio en curso.
4. **Comenzar ejercicio** → primera diapositiva. Cada diapositiva tiene 4 pasos:
   1. **Escenario y discusión**: se muestran los 4 textos (cada móvil ve el de su rol) con un contador del
      tiempo de discusión. Al llegar a cero no avanza solo: el admin inicia las propuestas.
   2. **Propuestas**: por cada rol se eligen N personas (N = número de alternativas) siguiendo una
      permutación aleatoria por rol (nadie repite hasta que todos los de su rol hayan propuesto). Tienen el
      tiempo configurado para escribir; el texto se guarda mientras escriben y, al acabarse el tiempo, se toma lo
      que alcanzaron a escribir. Se cierra solo al vencer el plazo o cuando todos enviaron.
   3. **Votación** (el admin da el inicio): todas las personas de cada rol, menos quienes propusieron, votan
      una de las propuestas de su rol (A, B, C…). Un voto, sin cambios. Se cierra sola al vencer el plazo o
      cuando todos votaron.
   4. **Resultados**: gráfico con votos y porcentaje por alternativa en cada card; todos ven los resultados de
      las 4 tarjetas (en el teléfono, la de su rol primero).
      Si hay **empate**, el panel muestra *Elegir como ganadora* en las alternativas empatadas; la elegida queda
      marcada con 🏆 para todos (si no se elige, todas las empatadas cuentan como ganadoras).
5. **Finalizar ejercicio** → el admin ve por persona: cuántas veces sugirió, no sugirió (le tocó y no escribió),
   su alternativa ganó (empates sin desempatar cuentan), votó y no votó (podía y no lo hizo).

**Temporizador:** durante la discusión, las propuestas o la votación el panel permite **⏸️ Pausar / ▶️ Reanudar**
y **➕ 15 s**. La presentación y los teléfonos muestran el contador (en pausa aparece ⏸).

**Vista previa:** en la línea de tiempo, *👁️ Vista previa* muestra cualquier diapositiva (con sus propuestas y
resultados si ya se jugó) solo al admin, sin cambiar lo que ven la presentación y los teléfonos; con ◀ ▶ se
recorren las demás y *Mostrar a participantes* la pone en pantalla.

**Diapositivas anteriores:** en el teléfono, *📜 Anteriores* muestra las diapositivas ya jugadas con el
escenario de su rol, su propuesta, su voto y los resultados. Si le toca proponer o votar, vuelve solo a la
diapositiva actual.

**Contextos separados** (activado por defecto): la presentación muestra solo el contexto general y los
teléfonos solo el escenario de su rol. En los pasos 1 y 2 la presentación muestra únicamente el contexto general a
pantalla completa (sin las 4 tarjetas); en propuestas, votación y resultados las tarjetas vuelven a aparecer. Desactivado, la presentación muestra los 5 textos y los teléfonos también
el contexto general. Se elige al crear la actividad y se cambia desde el panel; el panel del admin ve todo.

**Nombres de quienes proponen:** por defecto, la presentación y los teléfonos no muestran quién escribió
cada propuesta (solo cuántas personas proponen). Se elige al crear la actividad y se puede cambiar en cualquier
momento desde el panel. El panel del admin y los resultados por persona siempre muestran los nombres.

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
- `TRUSTED_PROXIES`: proxies cuyos `X-Forwarded-*` se respetan (por defecto, redes privadas).

### Producción con caddy-docker-proxy

`docker-compose.prod.yml` no trae Caddy: publica la app en la red externa del proxy mediante labels.

```bash
# .env: DOMAIN=rol.midominio.cl, POSTGRES_PASSWORD=..., (opcional) CADDY_NETWORK=caddy
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml logs app | grep -A4 "CUENTA ADMIN"
```

- `PUBLIC_URL` toma `https://$DOMAIN` y las cookies quedan como `Secure`.
- La app está en dos redes (la del proxy y la interna con Postgres). Si el proxy no tiene
  `CADDY_INGRESS_NETWORKS` configurado, podría elegir la IP equivocada; configúralo con el nombre de su red.
- **Con Cloudflare (proxy naranja)** usa el modo SSL **Full (strict)**. En modo *Flexible* Cloudflare
  conecta por HTTP, Caddy redirige a HTTPS y se produce un bucle de redirecciones infinito.

### Caché (Cloudflare u otros CDN)

La app envía `Cache-Control: no-store` y `Cloudflare-CDN-Cache-Control: no-store` en HTML y API, y en los
estáticos `no-cache` (el navegador revalida con ETag). Los JS/CSS se enlazan con un hash de su contenido
(`?v=…`), así que cada versión nueva usa URLs nuevas. Si tienes una regla de Cloudflare tipo *Cache Everything*
con *Edge TTL* forzado, quítala o exclúyela para este dominio (esas reglas ignoran los encabezados del origen).

### Detrás de otro proxy reverso (nginx, Traefik, Cloudflare…)

Si otro proxy ya termina HTTPS y reenvía a este servidor:

```env
SITE_ADDRESS=:80                       # no un dominio: si no, Caddy redirige a HTTPS → bucle infinito
PUBLIC_URL=https://rol.midominio.cl
COOKIE_SECURE=true
TRUSTED_PROXIES=private_ranges         # o la IP pública del proxy
```

El proxy externo debe enviar `X-Forwarded-Proto: https` y `Host` (casi todos lo hacen por defecto) y
apuntar al puerto 80 de este servidor.
- `POSTGRES_*`: credenciales de la base de datos.

Colores y emojis de los roles: `PROFILES` en `app/ciberrol/models.py`.
