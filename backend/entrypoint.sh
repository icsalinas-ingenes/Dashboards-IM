#!/bin/sh
set -e

# Si existe .env.enc, lo descifra con sops (requiere SOPS_AGE_KEY_FILE montado,
# ver docker-compose.yml) y arranca uvicorn con esas variables sin escribir
# nunca el .env en texto plano a disco. Si no existe, arranca con las
# variables de entorno que ya traiga el contenedor (útil para debug local).
if [ -f /app/.env.enc ]; then
  exec sops exec-env --input-type dotenv /app/.env.enc \
    "uvicorn main:app --host 0.0.0.0 --port 8000"
else
  echo "AVISO: no se encontró .env.enc — arrancando con el entorno del contenedor tal cual." >&2
  exec uvicorn main:app --host 0.0.0.0 --port 8000
fi
