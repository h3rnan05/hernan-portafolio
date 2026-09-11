#!/usr/bin/env bash
# Instala/actualiza todo lo necesario para el entorno de desarrollo del
# Portfolio Prediction Engine (backend FastAPI + frontend Next.js) y las
# suites de Python del bot de momentum. Idempotente: puede correr muchas
# veces sobre estado cacheado sin romperse.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# uv vive en ~/.local/bin; node/pnpm vienen de nvm. Los cargamos siempre
# porque el script no corre necesariamente en un shell de login.
export PATH="$HOME/.local/bin:$PATH"
export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
# shellcheck disable=SC1091
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"

# --- uv (binario estático; se instala si la imagen base no lo trae) ---------
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# --- PostgreSQL: instalar (si falta) y arrancar el clúster ------------------
# El backend usa Postgres real (Alembic + asyncpg), igual que producción con
# Supabase. Si la imagen base no lo trae, lo instalamos vía apt. Es idempotente:
# apt no reinstala si ya está y pg_ctlcluster devuelve error si ya corre.
if ! command -v pg_ctlcluster >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    postgresql-16 postgresql-client-16
fi
sudo pg_ctlcluster 16 main start 2>/dev/null || true
for _ in $(seq 1 30); do
  if sudo -u postgres pg_isready -q 2>/dev/null; then break; fi
  sleep 1
done

# --- rol + base de datos de desarrollo (idempotente) ------------------------
sudo -u postgres psql -v ON_ERROR_STOP=1 <<'SQL'
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'portfolio') THEN
    CREATE ROLE portfolio LOGIN PASSWORD 'portfolio';
  END IF;
END $$;
SQL
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='portfolio'" \
  | grep -q 1 || sudo -u postgres createdb -O portfolio portfolio

# --- .env del backend (solo dev local; está en .gitignore) ------------------
if [ ! -f backend/.env ]; then
  cat > backend/.env <<'ENV'
# Entorno de desarrollo local (Cloud Agent). No usar en producción.
DATABASE_URL=postgresql+asyncpg://portfolio:portfolio@localhost:5432/portfolio
DATABASE_URL_SYNC=postgresql+psycopg2://portfolio:portfolio@localhost:5432/portfolio
# Placeholder: solo el CLI de ingestión necesita una FRED key real; la API sirve sin ella.
FRED_API_KEY=dev-placeholder
ADMIN_BEARER_TOKEN=dev-token-change-me
ALLOWED_ORIGINS=http://localhost:3000,http://localhost:3340,http://127.0.0.1:3000,http://127.0.0.1:3340
LOG_LEVEL=INFO
ENV
fi

# --- .env.local del frontend ------------------------------------------------
if [ ! -f frontend/.env.local ]; then
  cat > frontend/.env.local <<'ENV'
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_SUPABASE_URL=
NEXT_PUBLIC_SUPABASE_ANON_KEY=
ENV
fi

# --- backend: dependencias + esquema + seed ---------------------------------
(
  cd backend
  uv sync --extra dev
  uv run alembic upgrade head
  uv run python scripts/seed_variables.py
)

# --- frontend: dependencias -------------------------------------------------
(
  cd frontend
  pnpm install --frozen-lockfile
)

# --- entorno Python compartido para momentum_hunter / paper_trader /
#     screener / telegram_bot (usan python3 plano, no uv) --------------------
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet pytest \
  -r momentum_hunter/requirements.txt \
  -r momentum_paper_trader/requirements.txt \
  -r screener/requirements.txt \
  -r telegram_bot/requirements.txt

echo "install.sh: entorno listo"
