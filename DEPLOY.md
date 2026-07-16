# Despliegue en servidor (Linux + Docker Compose)

Arquitectura: dos contenedores.
- `frontend` — build de React servido por nginx en el puerto 80 del contenedor
  (publicado en `8080` del host). Proxea `/api/*` al backend.
- `backend` — FastAPI/uvicorn en el puerto 8000, solo accesible dentro de la
  red interna de Docker (no expuesto al host).

El `.env` con la contraseña real de la base **nunca** viaja en texto plano:
se cifra con [`sops`](https://github.com/getsops/sops) usando una llave
[`age`](https://github.com/FiloSottile/age), y solo se descifra en memoria al
arrancar el contenedor del backend (`entrypoint.sh` usa `sops exec-env`, que
nunca escribe el `.env` descifrado a disco).

## 1. Requisitos en el servidor

```bash
# Docker + plugin de compose (Ubuntu/Debian)
curl -fsSL https://get.docker.com | sudo sh
sudo apt-get install -y docker-compose-plugin

# age (para generar la llave) — si tu distro no lo trae por apt:
sudo apt-get install -y age || \
  (curl -fsSL -o /tmp/age.tar.gz https://github.com/FiloSottile/age/releases/latest/download/age-v1.2.0-linux-amd64.tar.gz \
   && tar -xzf /tmp/age.tar.gz -C /tmp && sudo mv /tmp/age/age /tmp/age/age-keygen /usr/local/bin/)
```

## 2. Generar la llave age (una sola vez por servidor)

```bash
age-keygen -o age.key
# Imprime algo como: Public key: age1qy...un montón de caracteres...
sudo mkdir -p /etc/dashboards-im
sudo mv age.key /etc/dashboards-im/age.key
sudo chmod 600 /etc/dashboards-im/age.key
```

Guarda la **llave pública** (`age1...`) que imprimió — la necesitas en el
siguiente paso. La llave privada (`/etc/dashboards-im/age.key`) se queda solo
en este servidor, con permisos 600; si se pierde, tendrás que volver a cifrar
el `.env` con una llave nueva.

## 3. Configurar `.sops.yaml` y cifrar el `.env`

En tu máquina (o en el servidor, donde tengas el `.env` real con la
contraseña de la base):

1. Edita `.sops.yaml` en la raíz del repo y reemplaza
   `age1REEMPLAZA_ESTO_CON_TU_LLAVE_PUBLICA` por la llave pública del paso 2.
2. Instala `sops` si no lo tienes (`brew install sops` / descarga el binario
   de [releases](https://github.com/getsops/sops/releases)).
3. Cifra:
   ```bash
   sops -e backend/.env > backend/.env.enc
   ```
4. Verifica que `backend/.env.enc` sea ilegible (no debe mostrar la
   contraseña) y que `backend/.env` (texto plano) **no** se suba — ya está en
   `.gitignore`.

`backend/.env.enc` sí es seguro de commitear/copiar al servidor: sin la
llave privada de `/etc/dashboards-im/age.key` es inútil.

## 4. Llevar el código al servidor

Si el repo está en GitHub/GitLab:
```bash
git clone <tu-remoto> dashboards-im && cd dashboards-im
```
Si no, `rsync`/`scp` la carpeta completa (sin `node_modules`, `.venv`,
`backend/.env` en texto plano — ya excluidos por `.gitignore` si usas
`rsync --filter=':- .gitignore'`).

Confirma que `backend/.env.enc` llegó al servidor y que **no** llegó
`backend/.env` en texto plano.

## 5. Levantar los contenedores

```bash
docker compose up -d --build
```

Verifica:
```bash
docker compose logs -f backend   # debe arrancar uvicorn sin errores de conexión a MySQL
curl http://localhost:8080/api/catalogos   # debe responder JSON, no error
```

## 6. Exponerlo como página web (dominio + HTTPS)

`frontend` queda escuchando en `127.0.0.1:8080` del servidor. Dos opciones:

**A) Ya tienes nginx/Caddy en el servidor para otros sitios** — agrega un
`server`/`site` más que haga proxy_pass a `127.0.0.1:8080`, y certbot para el
certificado como ya lo haces con tus otros sitios.

**B) No tienes nada corriendo en el puerto 80/443 aún** — la forma más
simple es [Caddy](https://caddyserver.com/), que saca el certificado HTTPS
solo:
```bash
sudo apt-get install -y caddy
echo "tu-dominio.com {
    reverse_proxy 127.0.0.1:8080
}" | sudo tee /etc/caddy/Caddyfile
sudo systemctl restart caddy
```

## Actualizar después de cambios

```bash
git pull   # o rsync de nuevo
docker compose up -d --build
```

Si cambia el `.env` (nueva contraseña, nuevo host de base de datos, etc.):
```bash
sops -e backend/.env > backend/.env.enc
docker compose up -d --build backend
```

## Nota sobre CORS

`backend/main.py` hoy permite `allow_origins=["*"]` porque en dev el
frontend corre en otro puerto (5173) que el backend (8000). En este
despliegue, nginx sirve todo bajo el mismo origen (`/api/*` es proxy
interno), así que CORS ya no importa para el uso normal — se puede dejar
así o restringirlo a tu dominio si prefieres cerrarlo por completo.
