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
   sops -e backend/.env > backend/secrets.env
   ```
4. Verifica que `backend/secrets.env` sea ilegible (no debe mostrar la
   contraseña) y que `backend/.env` (texto plano) **no** se suba — ya está en
   `.gitignore`.

`backend/secrets.env` sí es seguro de commitear/copiar al servidor: sin la
llave privada de `/etc/dashboards-im/age.key` es inútil.

## 4. Llevar el código al servidor

Si el repo está en GitHub/GitLab:
```bash
git clone <tu-remoto> dashboards-im && cd dashboards-im
```
Si no, `rsync`/`scp` la carpeta completa (sin `node_modules`, `.venv`,
`backend/.env` en texto plano — ya excluidos por `.gitignore` si usas
`rsync --filter=':- .gitignore'`).

Confirma que `backend/secrets.env` llegó al servidor y que **no** llegó
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

## 6. Exponerlo como página web (Cloudflare Tunnel)

El servidor solo es accesible por VPN, así que en vez de abrir puertos o
depender de una IP pública, se usa un túnel saliente de Cloudflare:
`cloudflared` corre en el servidor y expone `frontend` (puerto `8080`) como
una URL pública (`https://dashboard-fiv.tudominio.com`), sin tocar el
firewall ni la VPN.

**Riesgo a tener presente**: el backend (`main.py`) todavía no tiene login
(`# >>> ROLES` está pendiente) — mientras solo vivía en la VPN, la VPN
actuaba como filtro. En cuanto el túnel esté activo, cualquiera con el link
puede ver el dashboard. Si en algún momento quieres restringirlo a personas
concretas por correo, Cloudflare Access (Zero Trust, gratis) se agrega
después sin cambiar nada de esto.

### 6.1 Dominio en Cloudflare

Si tu dominio no está ya en Cloudflare: crea una cuenta gratis, agrégalo
("Add a site") y cambia los *nameservers* en tu registrador a los que te
indique Cloudflare. Si el dominio ya tiene correo (MX) u otros registros,
verifica que se hayan migrado antes de cortar el DNS viejo.

### 6.2 Instalar y autenticar `cloudflared` en el servidor

```bash
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo gpg --dearmor -o /usr/share/keyrings/cloudflare-main.gpg
echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared $(lsb_release -cs) main" \
  | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt-get update && sudo apt-get install -y cloudflared

cloudflared tunnel login
# Abre un link — lo visitas en tu navegador y autorizas contra tu cuenta
# de Cloudflare y el dominio del paso 6.1.
```

### 6.3 Crear el túnel y apuntarlo al frontend

```bash
cloudflared tunnel create dashboard-fiv
# Guarda el Tunnel ID que imprime y la ruta del archivo de credenciales
# (algo como ~/.cloudflared/<tunnel-id>.json) — es un secreto, mismo trato
# que age.key: nunca a git, permisos 600.

sudo mkdir -p /etc/cloudflared
sudo mv ~/.cloudflared/*.json /etc/cloudflared/credentials.json
sudo chmod 600 /etc/cloudflared/credentials.json
```

Crea `/etc/cloudflared/config.yml`:
```yaml
tunnel: dashboard-fiv
credentials-file: /etc/cloudflared/credentials.json

ingress:
  - hostname: dashboard-fiv.tudominio.com
    service: http://localhost:8080
  - service: http_status:404
```

Enruta el DNS y deja el túnel corriendo como servicio:
```bash
cloudflared tunnel route dns dashboard-fiv dashboard-fiv.tudominio.com
sudo cloudflared service install
sudo systemctl enable --now cloudflared
```

Verifica en `https://dashboard-fiv.tudominio.com` desde fuera de la VPN
(por ejemplo, con datos móviles).

## Actualizar después de cambios

```bash
git pull   # o rsync de nuevo
docker compose up -d --build
```

Si cambia el `.env` (nueva contraseña, nuevo host de base de datos, etc.):
```bash
sops -e backend/.env > backend/secrets.env
docker compose up -d --build backend
```

## Nota sobre CORS

`backend/main.py` hoy permite `allow_origins=["*"]` porque en dev el
frontend corre en otro puerto (5173) que el backend (8000). En este
despliegue, nginx sirve todo bajo el mismo origen (`/api/*` es proxy
interno), así que CORS ya no importa para el uso normal — se puede dejar
así o restringirlo a tu dominio si prefieres cerrarlo por completo.
