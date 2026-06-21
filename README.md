# Pomodoro Personalizado — Backend

Backend Django/DRF para gerenciamento de atividades, categorias, grupos, agendamentos e históricos do aplicativo Pomodoro.

## Requisitos

- Python 3.12;
- Poetry;
- Docker;
- PostgreSQL 16 central local em `/home/barscka/workspace/postgres`.

## PostgreSQL de desenvolvimento

O projeto reutiliza o stack central local. Não execute outro PostgreSQL por este repositório.

Suba e valide o stack:

```bash
cd /home/barscka/workspace/postgres
docker network inspect backend_net >/dev/null 2>&1 \
  || docker network create backend_net
docker compose up -d
docker compose ps
docker exec postgres pg_isready -h 127.0.0.1 -p 5432
```

O Django executado no host conecta em `127.0.0.1:5432`. A porta do container local deve permanecer vinculada apenas a localhost.

Cada aplicação possui banco e usuário próprios. Este backend usa:

```text
Banco: pomodoro_task_dev
Usuário: pomodoro_task_dev_user
```

Credenciais reais ficam em `.env.local`, que não é versionado. Use [.env.example](.env.example) como referência.

## Instalação

```bash
poetry install
cp .env.example .env.local
```

Preencha a senha local em `.env.local` e execute:

```bash
poetry run python manage.py check
poetry run python manage.py migrate --noinput
poetry run python manage.py runserver
```

## Testes

A suíte padrão usa exclusivamente SQLite isolado, independentemente das credenciais PostgreSQL locais:

```bash
poetry run python manage.py test
```

Os testes não usam o banco de desenvolvimento, homologação ou produção. O arquivo temporário fica em `tests/.tmp/`, ignorado pelo Git.

## Migração do SQLite legado

O arquivo `db.sqlite3` original nunca deve receber as migrations de transporte diretamente. Trabalhe com uma cópia explícita:

```bash
mkdir -p /tmp/pomodoro-migration
cp -p db.sqlite3 /tmp/pomodoro-migration/source.sqlite3
sha256sum db.sqlite3 /tmp/pomodoro-migration/source.sqlite3
```

Atualize somente a cópia:

```bash
LEGACY_SQLITE_PATH=/tmp/pomodoro-migration/source.sqlite3 \
DJANGO_SETTINGS_MODULE=config.settings.legacy_sqlite \
poetry run python manage.py migrate --noinput
```

Exporte apenas dados de aplicação e autenticação que devem ser preservados:

```bash
LEGACY_SQLITE_PATH=/tmp/pomodoro-migration/source.sqlite3 \
DJANGO_SETTINGS_MODULE=config.settings.legacy_sqlite \
poetry run python manage.py dumpdata \
  auth.user \
  rest_framework_api_key.apikey \
  user_profile \
  pomodoro.category \
  pomodoro.activity \
  pomodoro.schedule \
  pomodoro.history \
  --natural-foreign \
  --natural-primary \
  --indent 2 \
  --output /tmp/pomodoro-migration/data.json
```

Depois de executar as migrations em um PostgreSQL vazio, importe a fixture:

```bash
poetry run python manage.py migrate --noinput
poetry run python manage.py loaddata /tmp/pomodoro-migration/data.json
```

Antes de liberar uso, compare contagens, maiores PKs, relacionamentos, sequences e autenticação. Não importe a fixture repetidamente em um banco já carregado.

## Produção

O PostgreSQL da VPS não publica a porta 5432. O backend de produção deve executar em container, participar da rede externa `backend_net` e usar:

```env
DB_HOST=postgres
DB_PORT=5432
```

O banco e o usuário de produção são diferentes dos locais. Consulte [a spec de migração](docs/specs/MIGRACAO_POSTGRES.md) antes do deploy.
