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
