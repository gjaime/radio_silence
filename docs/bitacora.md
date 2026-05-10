# Bitácora de actividades

Registro cronológico de sesiones de trabajo en el proyecto.

---

## 2026-05-09 — Sesión 1 + 2: construcción completa del sistema

### Contexto inicial
- Monitor `radio_auditor` corriendo en laptop con 2 emisoras de Radio Fórmula Bajío (León) desde 2026-05-08.
- Objetivo: migrar a producción, escalar a 18 emisoras y construir dashboard público.

### Lo que se hizo

#### Migración de datos históricos
- Carga de CSVs históricos (2026-05-08 y 2026-05-09) a TimescaleDB en producción con cutoff `2026-05-09 19:08:00`.
- Verificación de 18 monitores corriendo como `monitor@.service` en CT240.
- Detención de procesos locales (laptop) tras confirmar producción estable.

#### Base de datos — vistas materializadas
- Diseño e implementación de 3 MVs para alimentar el dashboard sin queries costosos en tiempo real:
  - `mv_status_5min` — cubetas 5 min, últimas 25 h (tab 24 h)
  - `mv_status_1h`   — cubetas 1 h, últimas 101 h (tab 100 h)
  - `mv_incidents_live` — gap-and-island, últimas 101 h, ≥ 3 s
- Función `classify_alert(duration_seconds)` con 7 niveles de severidad.
- Índices únicos en las dos primeras MVs para `REFRESH CONCURRENTLY`.
- Cron root en CT240: `* * * * *` ejecutando `refresh_views.py` (~1.2 s por ciclo).
- Permisos: `GRANT SELECT ON ... TO radio`, `ALTER MATERIALIZED VIEW ... OWNER TO radio`.

#### API — radio_silence (este repo)
- Creación del repo separado `/home/gjaime/Documentos/git/radio_silence`.
- FastAPI con dos endpoints:
  - `GET /api/status?hours=24|100`
  - `GET /api/incidents?hours=24|100`
- Cálculo de uptime sobre tiempo monitoreado (no ventana completa), con `coverage_pct` como métrica auxiliar.
- Archivos estáticos servidos desde el mismo proceso uvicorn.
- `systemd/silence-api.service` desplegado en CT240 puerto 8080.

#### Dashboard HTML
- Single-page app en `static/index.html`:
  - Tabs 24 h / 100 h con auto-refresh cada 60 s.
  - Barras de color por cubeta temporal con tooltip al hover.
  - Dos métricas de uptime por emisora: **en línea** y **al aire**.
  - Tabla de incidentes con badges de severidad (7 niveles).
  - Favicon emoji 📻.
  - Mobile-responsive (media query 640 px).
  - Fix de barra gris trailing: retrocede un bucket si el actual tiene < 30 s.
- Modal (?) en el header con explicación completa:
  - Colores y qué representan.
  - Métricas de uptime y fórmulas.
  - Tipos de incidente (Silencio vs Caída).
  - 7 niveles de severidad con duraciones.
  - Nota sobre limitación ISP.
- Link a GitHub en el footer.

#### Producción
- Despliegue en CT240 (10.13.69.240, pve02) en `/opt/radio_silence`.
- Cloudflared tunnel en CT201 → `silence.kraken-lab.work`.
- CNAME en Cloudflare apuntando al túnel.
- Workflow de deploy: `rsync` laptop → pve02 → `pct push` CT240 → `systemctl restart`.

#### Depuración y correcciones
- `InsufficientPrivilege` en MVs → GRANT + ALTER OWNER.
- Cron sin persistir → verificado con `crontab -l`, reinstalado.
- `mv_incidents_live` mostraba estaciones inactivas → añadido `AND s.active = TRUE` en el CTE.
- La Poderosa y La Rancherita León desactivadas (`active = FALSE`) — streams incorrectos.
- Uptime mal calculado (denominador incorrecto) → iteración hasta fórmula correcta: `sampled / sampled`.
- Barra gris al extremo derecho del gráfico → fix `alignedNow` con umbral 30 s.

#### Documentación y GitHub
- Repo publicado en GitHub: `gjaime/radio_silence` (público).
- README con concepto, arquitectura, quick start, clasificación de fallas completa (tablas inline), roadmap.
- `docs/architecture.md` — diagrama de sistema, topología, multi-sitio y SDR como roadmap.
- `docs/data-model.md` — esquema completo, MVs, función `classify_alert`, fórmulas de uptime.
- `docs/data-governance.md` — frescura, retención (30 días), acceso, runbook operativo.
- `docs/fault-taxonomy.md` — tipos de incidente, 7 niveles de severidad, ciclo de vida, validación RF.
- `docs/img/` — directorio reservado para screenshots (pendiente).

### Estado al cierre de sesión
- `silence.kraken-lab.work` en producción y público.
- 16 emisoras activas monitoreadas (18 instaladas, 2 desactivadas).
- Refresh de MVs cada minuto, dashboard auto-refresh cada 60 s.
- Repo público con documentación completa.

### Pendiente para próximas sesiones
- Tomar screenshots del dashboard y agregarlos a `docs/img/`.
- Localizar streams correctos de La Poderosa 93.9 y La Rancherita 105.1 León.
- Grafana: datasource PostgreSQL + dashboard de series de tiempo y alertas.
- Notificaciones por webhook/email al abrir/cerrar incidente.
- Considerar arquitectura multi-sitio para eliminar dependencia del ISP.

---

## 2026-05-10 — Sesión 3: escalado a 54 emisoras, RAM fix, investigación top-40

### Contexto
Continuación de la sesión anterior (partida en dos por límite de contexto).

### Cambios técnicos

#### RAM CT240 — Crisis de ffmpeg
- **Síntoma:** 16 de 59 estaciones offline simultáneamente.
- **Causa raíz:** 59 procesos ffmpeg consumían >2 GB RAM disponible → OOM silencioso.
- **Solución:** `pct set 240 --memory 4096` en caliente (sin reinicio). RAM de 2 → 4 GB.
- **Resultado:** Todas las estaciones se recuperaron en <60 s.

#### Borrado de datos históricos
- `DELETE FROM radio_monitor WHERE station_id NOT IN ('mx_gto_leon_101100','mx_gto_leon_107100')` → 213,796 filas eliminadas.
- Motivo: datos inválidos durante el setup (estaciones sin stream correcto, RAM insuficiente).
- Sólo se conservó el histórico limpio de Fórmula Bajío 101.1 y 107.1 León.

#### Fix stream CDMX 104.1
- URL anterior `http://` → actualizada a `https://mdstrm.com/audio/61e1dfd2658baf082814e25d/live.m3u8`.
- Monitor reiniciado; sin errores desde entonces.

#### Escalado a 54 emisoras activas (commit ad9c3eb)
- Se agregaron 43 emisoras nuevas cubriendo CDMX, GDL, Monterrey, Querétaro, León, Tijuana, etc.
- Se implementó toggle "agrupar por cadena / por estado" en el dashboard.
- Source: investigación de streams vía StreamTheWorld, RadioJar, Zeno.fm, Audiorama, etc.

#### Fórmula Cancún 92.3 FM — Desactivada
- Stream `https://stream.radiojar.com/285mz7q2q8tvv` responde correctamente (70 KB de datos).
- Audio consistentemente -54 a -63 dBFS (umbral: -45 dBFS) → `SILENCIO` en cada muestra.
- Diagnóstico: la estación transmite dead air real, no es error de URL.
- Acción: `active = FALSE`, monitor detenido, notas en BD.
- **Pendiente:** encontrar URL alternativa o confirmar que la estación está fuera del aire.

### Investigación top-40 (emisoras.com.mx/genero/top-40/)

Se hizo un cruce entre el top-40 de emisoras.com.mx y la BD actual. Se encontraron ~40 estaciones faltantes y se investigaron URLs de stream para cada una.

#### Streams verificados y listos para insertar (próxima sesión)

| Estación | Freq | Ciudad | Estado | Stream URL |
|----------|------|--------|--------|-----------|
| Oye FM | 89.7 FM | CDMX | CDMX | `https://acp2.lorini.net/10128/stream` |
| Azul 89 (ACIR online) | 89.3 | CDMX | CDMX | `https://playerservices.streamtheworld.com/api/livestream-redirect/ACIR24_s01AAC.aac` |
| Pop FM | 98.7 FM | CDMX | CDMX | `https://ice42.securenetsystems.net/POP` |
| Fusión | 90.1 FM | Boca del Río | Veracruz | `https://fusion.centrocibernetico.com/` |
| Arroba FM | 88.5 FM | Chihuahua | Chihuahua | `https://stream.zeno.fm/hv5u0bg9d78uv` |
| Súper 92.5 | 92.5 FM | Chihuahua | Chihuahua | `https://streamingcwsradio30.com:7104/` |
| Radio Lobo MX | online | Chihuahua | Chihuahua | `https://sp2.servidorrprivado.com/9302/stream` |
| Exa FM | 91.7 FM | Tijuana | BC | `https://playerservices.streamtheworld.com/api/livestream-redirect/XHGLX.mp3` |
| Exa FM | 91.5 FM | Mexicali | BC | `https://playerservices.streamtheworld.com/api/livestream-redirect/XHJCFM.mp3` |
| Invasora | 104.9 FM | Mexicali | BC | `https://streamingcwsradio30.com:7039/stream` |
| Pulsar | 107.3 FM | Tijuana | BC | `https://streamingcwsradio30.com:7021/stream` |
| Exa FM | 104.1 FM | Ensenada | BC | `https://playerservices.streamtheworld.com/api/livestream-redirect/XHADA_SC` |
| Estéreo Sol | 101.1 FM | Ensenada | BC | `https://edge.mixlr.com/channel/gdjut` |
| Amor Mío | 92.9 FM | Ensenada | BC | `https://sudominiohoy.com:18303/;stream.mp3` ⚠️ FAIL |
| Vida | 1370 AM | Mexicali | BC | `https://sonic.sistemahost.es/8056/stream` |
| Exa FM | 97.3 FM | Aguascalientes | Aguascalientes | `https://playerservices.streamtheworld.com/api/livestream-redirect/XHAGC.mp3` |
| Digital 105 | 105.3 FM | Aguascalientes | Aguascalientes | `http://201.159.48.106:8080/` |
| Magia 101 | 101.7 FM | Aguascalientes | Aguascalientes | `http://201.159.48.107:8080/` |
| Exa FM | 100.3 FM | Campeche | Campeche | `https://playerservices.streamtheworld.com/api/livestream-redirect/XHMI.mp3` |
| Delfín | 88.9 FM | Cd. del Carmen | Campeche | `http://stream.zeno.fm/4u09mv8m1k0uv` |
| Máxima 98.9 | 98.9 FM | Cd. del Carmen | Campeche | `http://www.tuasesorweb.com:8016/` |
| Exa FM | 95.7 FM | Comitán | Chiapas | `https://streaming.servicioswebmx.com/8242/stream` |
| Los 40 | 96.1 FM | Tuxtla Gutiérrez | Chiapas | `https://stream.freepi.io/8308/live` |
| Extremo | 90.7 FM | Tapachula | Chiapas | `https://stream.freepi.io/8310/live` |
| W Radio | 91.5 FM | Tapachula | Chiapas | `https://streaming.servicioswebmx.com/8248/stream` |
| Cabo Mil | 96.0 FM | San José del Cabo | BCS | `https://s2.radio.co/s77395f3d5/listen` |

#### Streams pendientes de encontrar (próxima sesión)

| Estación | Freq | Ciudad | Estado | Problema |
|----------|------|--------|--------|---------|
| D95 | 94.9 FM | Chihuahua | Chihuahua | TuneIn: "not compatible" |
| La Lupe | 104.5 FM | Chihuahua | Chihuahua | STW call sign incorrecto (XHHEMFMAAC falla) |
| Latina | 104.5 FM | Tijuana | BC | STW XLTNFMAAC falla; TuneIn da Finlandia |
| Máxima | 106.7 FM | Guadalajara | Jalisco | TuneIn no tiene resultado correcto |
| Los 40 | 570 AM | Torreón | Coahuila | No encontrado en TuneIn |
| Exa FM | 98.5 FM | Tuxtla Gutiérrez | Chiapas | Zeno URL falla |
| Exa FM | 91.5 FM | Cd. Acuña | Coahuila | gvstream.net falla |
| Exa FM | 99.7 FM | Cd. del Carmen | Campeche | radiorama.mx:2019 falla |
| Lupe | 101.7 FM | Parral | Chihuahua | Multimedios station3 → 404 |
| Amor Mío | 92.9 FM | Ensenada | BC | sudominiohoy.com falla |
| Digimix 95 | 95.9 FM | La Paz | BCS | No encontrado |
| Radium | ~95.1 FM | La Paz | BCS | No encontrado |
| Vida | ~1400 AM | Piedras Negras | Coahuila | URL anterior falla |

### Notas importantes
- El slug `fusion` en emisoras.com.mx es **Fusión 90.1 FM de Boca del Río, Veracruz** (XHLL-FM), NO una estación de CDMX. 105.7 CDMX es Reactor 105 (IMER).
- Súper FM (slug `super`) = **Súper 92.5 FM, Chihuahua** (Radiorama XHEFO-FM), no CDMX.
- D95 y La Lupe son estaciones de Multimedios Radio en Chihuahua; su streaming.multimedios.com no tiene endpoints claros.
- Vida Mexicali = XEHG-AM 1370 AM, Grupo Audiorama. Stream `sonic.sistemahost.es:8056` verificado OK.
- Digital y Magia Aguascalientes: IPs directas `201.159.48.106/107:8080` — son del mismo proveedor (Radiogrupo).

### Estado al cierre
- 54 estaciones activas en producción.
- Fórmula Cancún desactivada (silent stream).
- 26 streams nuevos identificados y verificados, pendientes de INSERT.
- 13 streams pendientes de investigar.

### Pendientes para próxima sesión
- [ ] Insertar las 26 estaciones listas (ver tabla arriba) con SQL INSERT en CT240.
- [ ] Investigar streams de D95, La Lupe, Latina Tijuana (posiblemente vía Multimedios API o páginas web directas).
- [ ] Investigar Máxima Guadalajara 106.7 FM (Promomedios).
- [ ] Investigar Los 40 Torreón 570 AM.
- [ ] Investigar Exa Tuxtla, Exa Acuña, Exa Carmen, Lupe Parral, Digimix/Radium La Paz.
- [ ] Revisar Fórmula Cancún: buscar stream alternativo en página oficial radioformula.com.mx/cancun.
- [ ] Tomar screenshots del dashboard para `docs/img/`.
