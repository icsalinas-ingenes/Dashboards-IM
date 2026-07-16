# Dashboard FIV — Fase 1

## Qué incluye
- `frontend/` — proyecto Vite + React. `src/DashboardBlastos.jsx` es el shell completo (sidebar + dashboard interactivo, pregunta 1: tasa de blastos). Trae datos de muestra para verlo funcionando; sustituir por llamadas al API.
- `backend/` — FastAPI con el SQL real contra `gran_consolidado_fiv`. Sin joins.

## Correr el backend (Windows / PowerShell)
```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# Editar backend\.env con la password real (ya trae host/puerto/usuario/base)
uvicorn main:app --reload
# docs interactivos en http://localhost:8000/docs
```

La conexión vive en `backend\.env` (no se sube al repo, ver `.gitignore`):
```
DB_URL=mysql+pymysql://izsalinas:TU_PASSWORD@201.174.189.206:4417/Ingenes?charset=utf8mb4
```

## Correr el frontend (requiere Node.js LTS instalado)
```powershell
cd frontend
npm install
npm run dev
# abre http://localhost:5173
```
Vite ya tiene configurado un proxy: las llamadas a `/api/...` se reenvían automáticamente al backend en `http://localhost:8000` (ver `vite.config.js`), así que no hace falta CORS extra ni hardcodear la URL completa.

## Conectar el front al API
En `DashboardBlastos.jsx`, reemplazar el bloque `RAW`/`useMemo` de agregación por:
```js
const params = new URLSearchParams({ desde, hasta, numerador: numKey, denominador: denKey, pais });
medicos.forEach(m => params.append("medico", m));
sucursales.forEach(s => params.append("sucursal", s));
const data = await fetch(`/api/dashboards/blastos?${params}`).then(r => r.json());
// data.kpis, data.serie, data.ranking.medico, data.ranking.sucursal, data.tabla
```
La forma del JSON del API ya coincide con lo que pinta el componente.

## Decisiones tomadas
- Tasa = `Σ blastos / Σ óvulos`, nunca promedio de razones por ciclo.
- Numerador y denominador seleccionables (whitelist en backend, jamás texto libre del usuario).
- `país` derivado de `lugar_de_procedencia` (US: Orange County, Houston, San Diego, McAllen).
- Filtro `origen_de_ovulos` visible: comparar propios vs ovodonación mezclados no es justo entre médicos.
- Roles: todos ven todo por ahora. Punto de inyección marcado con `# >>> ROLES` en `main.py`.

---

## Pendiente antes de la pregunta 2 (tasa de embarazo)

El join `gran_consolidado_fiv ↔ seguimiento_betas` dio **0% de match** (1 de 3826).
Dos cosas que resolver:

1. **`seguimiento_betas` solo tiene positivos** → es el numerador, no el universo.
   El denominador (todas las punciones) sale de `gran_consolidado_fiv`. La tasa es
   una verificación de existencia: ¿el protocolo aparece como positivo?

2. **El formato de protocolo no coincide.** Correr esto para verlo lado a lado:
```sql
-- ¿Cómo se escribe el protocolo en cada tabla? (mismos años)
SELECT 'consolidado' AS t, n_de_protocolo FROM gran_consolidado_fiv
WHERE n_de_protocolo LIKE 'I%22%' LIMIT 10;
SELECT 'betas' AS t, n_de_protocolo FROM seguimiento_betas
WHERE n_de_protocolo LIKE 'I%22%' LIMIT 10;

-- Prueba de cobertura en un año MADURO (no 2026, que aún no tiene desenlace):
SELECT COUNT(*) ciclos,
       SUM(CASE WHEN sb.n_de_protocolo IS NOT NULL THEN 1 ELSE 0 END) con_beta
FROM gran_consolidado_fiv g
LEFT JOIN seguimiento_betas sb
  ON UPPER(REPLACE(TRIM(sb.n_de_protocolo),' ','')) = UPPER(REPLACE(TRIM(g.n_de_protocolo),' ',''))
WHERE YEAR(g.fecha_de_puncion) = 2022;
```
Si en 2022 la cobertura sigue siendo ~0%, el problema es de formato y hay que
ver los dos LIKE de arriba. Si sube a 60-80%, el 0% de 2026 era solo recencia
(los ciclos nuevos aún no tienen resultado) y el join sirve tal cual.
