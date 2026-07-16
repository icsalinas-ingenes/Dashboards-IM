#!/bin/sh
set -e

# Si existe secrets.env (el .env cifrado con sops), lo descifra y arranca
# uvicorn con esas variables sin escribir nunca el .env en texto plano a
# disco (requiere SOPS_AGE_KEY_FILE montado, ver docker-compose.yml).
# El nombre "secrets.env" es a propósito: `sops exec-env` no acepta
# --input-type, detecta el formato dotenv por la extensión ".env".
# Si no existe, arranca con las variables de entorno que ya traiga el
# contenedor (útil para debug local).
if [ -f /app/secrets.env ]; then
  exec sops exec-env /app/secrets.env \
    "uvicorn main:app --host 0.0.0.0 --port 8000"
else
  echo "AVISO: no se encontró secrets.env — arrancando con el entorno del contenedor tal cual." >&2
  exec uvicorn main:app --host 0.0.0.0 --port 8000
fi
