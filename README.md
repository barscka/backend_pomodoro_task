# Pomodoro Personalizado — Backend

Backend Django/DRF para gerenciamento de atividades, categorias, grupos, agendamentos e históricos do aplicativo Pomodoro.

## Contratos da API

`POST /api/activities/<id>/start/` e idempotente:

- `201 Created` quando cria uma nova execucao;
- `200 OK` quando reutiliza a execucao aberta da mesma atividade;
- nunca retorna `304`.

Atividades sem categoria explicita passam a usar a categoria padrao `Todos` com `id = 1`.

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
  pomodoro.group \
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

O PostgreSQL da VPS não publica a porta 5432. O backend participa simultaneamente de:

- `app_net`: rede interna entre Nginx e Gunicorn;
- `backend_net`: rede externa compartilhada exclusivamente para alcançar PostgreSQL.

O Nginx do container é publicado somente em `127.0.0.1:8080`. A imagem do Nginx é construída a partir de [docker/nginx/Dockerfile](docker/nginx/Dockerfile) e recebe a configuração versionada em `docker/nginx/nginx.conf` no build. O Nginx já instalado no host continua responsável pela porta pública 80 e encaminha as requisições para esse endereço.

```text
Internet HTTP :80
       |
Nginx do host
       |
127.0.0.1:8080
       |
Nginx container -> app_net -> Gunicorn/Django -> backend_net -> PostgreSQL
```

### Limitação atual: HTTP sem TLS

Este deploy não configura HTTPS, conforme a infraestrutura atual. API keys, cookies e conteúdo trafegam sem criptografia entre o cliente e a VPS. Não use esse desenho em redes não confiáveis nem trate os quatro alertas HTTPS do `manage.py check --deploy` como resolvidos.

Quando houver certificado, será necessário habilitar TLS no Nginx do host, cookies `Secure`, redirecionamento HTTPS e HSTS após validação.

### Preparação

O banco de produção deve possuir usuário exclusivo e não pode pertencer ao administrador central:

```text
Banco: pomodoro_task_prod
Usuário: pomodoro_task_user
```

Crie `.env` a partir do exemplo e preencha valores reais sem versioná-los:

```bash
cp .env.example .env
chmod 600 .env
```

As variáveis de banco no container devem usar:

```env
DB_HOST=postgres
DB_PORT=5432
```

Valide a rede e a configuração:

```bash
docker network inspect backend_net
docker compose -f compose.yml config --quiet
```

### Build e release

Construa a imagem e execute migrations como uma etapa única antes de iniciar os workers:

```bash
docker compose -f compose.yml build --pull
docker compose -f compose.yml run --rm backend python manage.py migrate --noinput
docker compose -f compose.yml run --rm backend python manage.py collectstatic --noinput
docker compose -f compose.yml up -d
docker compose -f compose.yml ps
```

No primeiro cutover, carregue a fixture final do SQLite da VPS depois de `migrate` e antes de `up -d`. Não reutilize a fixture do ensaio local.

### Nginx do host

Use [o exemplo versionado](deploy/nginx/backend_pomodoro_task.conf.example), substitua `server_name`, valide e recarregue:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

O proxy antigo apontava diretamente para Gunicorn em `127.0.0.1:8000`; a configuração nova deve apontar para o Nginx do container em `127.0.0.1:8080`.

Somente depois que o novo health check responder, desative o programa antigo no Supervisor:

```bash
curl --fail http://127.0.0.1:8080/healthz/
docker compose -f compose.yml logs --tail=100 backend nginx
```

### Rollback

Antes de liberar novas escritas no PostgreSQL, o rollback consiste em parar o Compose, restaurar a configuração anterior do Nginx e reativar o processo antigo no Supervisor com o SQLite preservado.

Depois de novas escritas no PostgreSQL, retornar diretamente ao SQLite perde dados. Nesse caso, prefira correção progressiva ou prepare uma migração reversa em nova janela de manutenção.

Consulte [a spec de migração](docs/specs/MIGRACAO_POSTGRES.md) antes do deploy.
