# Pomodoro Personalizado — Backend

Backend Django/DRF para gerenciamento de atividades, categorias, grupos, agendamentos e históricos do aplicativo Pomodoro.

## Contratos da API

`POST /api/activities/<id>/start/` e idempotente:

- `201 Created` quando cria uma nova execucao;
- `200 OK` quando reutiliza a execucao aberta da mesma atividade;
- nunca retorna `304`.

Atividades sem categoria explicita passam a usar a categoria padrao `Todos` com `id = 1`.

### Recriação e prévia da fila

`POST /api/activity-queue/recreate/` substitui explicitamente a fila normal ativa do
grupo, preservando a fila anterior e seu histórico. O cliente deve enviar a fila que
conhece para evitar recriações duplicadas:

```json
{
  "group_id": 3,
  "expected_queue_id": 10
}
```

A resposta `201 Created` informa a nova `queue_id`, o vínculo
`recreated_from_queue_id`, o tamanho total da fila e as atividades puladas que deixaram
de ser elegíveis. Filas de revisão e escopos com execução aberta retornam conflito.

`GET /api/activity-queue/activities/?group_id=3` consulta, sem criar ou avançar a fila,
os primeiros 30 itens operacionais ordenados por posição. A resposta inclui
`available_count`, `has_more` e estatísticas históricas de conclusões e pulos por
atividade. O limite de 30 afeta somente a prévia, nunca o tamanho persistido da fila.

## Metas semanais

O backend oferece metas recorrentes de `minutes` ou `sessions`, por grupo ou categoria,
isoladas pelo mesmo Authorization usado nas execuções. Uma chave compartilhada implica
metas compartilhadas; rotação de chave não migra metas automaticamente.

| Método | Rota | Uso |
| --- | --- | --- |
| POST | `/api/weekly-goals/` | Criar meta |
| GET | `/api/weekly-goals/` | Listar; filtro opcional `active=true` ou `false` |
| GET | `/api/weekly-goals/<id>/` | Consultar configuração e versão |
| PATCH | `/api/weekly-goals/<id>/` | Editar alvo ou ativação com `expected_version` |
| GET | `/api/weekly-goals/progress/` | Progresso, destino descritivo e contadores de pulos da semana atual ou `week_start=YYYY-MM-DD` |

O progresso preserva os campos originais e inclui `destination` e
`activity_signals`, com `skip_count` (ações de pular) separado de
`distinct_activities_skipped` (atividades distintas). Para receber também as três
atividades mais puladas, use
`GET /api/weekly-goals/progress/?include=activity_signals`. Valores desconhecidos
de `include` retornam `400 invalid_include`. Pulos são apenas sinais contextuais:
não alteram o progresso nem a ordenação da fila.

Cadastro de exemplo:

```json
{"metric":"minutes","group_id":3,"target":240}
```

Para categoria, substituir `group_id` por `category_id`. A resposta inclui `id` e
`version`. Exemplo de edição:

```json
{"target":300,"expected_version":1}
```

Versão obsoleta ou cadastro duplicado retorna 409. Desativar com
`{"active":false,"expected_version":2}`; não há DELETE. Métrica e destino são imutáveis.
Edições valem para a semana atual e seguintes, preservando revisões de semanas anteriores.
Listagens usam `page` e `page_size` (20 por padrão, máximo 100).

Semanas começam segunda-feira em `America/Sao_Paulo`. O progresso considera somente
conclusões persistidas, pela data de conclusão, e retorna alvo, realizado, restante,
percentual decimal e atingimento. Consultas não alteram execuções. O campo
`pending_reconciliation_count` informa sessões vencidas ainda abertas no período e
escopo; o cliente pode reconciliá-las pela rota de execução existente antes de atualizar
o progresso. Minutos são inteiros; conclusão antecipada com zero minutos conta uma sessão.

O grupo Todos agrega todos os grupos de origem. Sessões mantêm sua classificação do
início e duração concluída mesmo após alterações ou exclusão das atividades.

### Aplicação da migração e carga histórica

A migration `0017_weekly_goals` é aditiva. No ambiente de destino, aplicar a migração
antes de iniciar a versão nova. Após iniciar a captura de conclusões, carregar o legado
antes de liberar o uso de metas aos clientes:

```bash
poetry run python manage.py migrate --noinput
poetry run python manage.py backfill_goal_completions --dry-run
poetry run python manage.py backfill_goal_completions --batch-size 500
poetry run python manage.py backfill_goal_activity_skips --dry-run
poetry run python manage.py backfill_goal_activity_skips --batch-size 500
```

Esses são comandos de implantação; não fazem parte da suíte de testes. O dry-run
somente relata quantidades. A carga é idempotente e não altera History ou contadores.
Registros sem escopo, abertos ou inconsistentes são excluídos com motivos no relatório.
Quando não há snapshot do início, a classificação atual é marcada como `legacy_current`.
Edições manuais posteriores do histórico não reescrevem fatos já carregados.

Contrato completo e limites: [SPEC-BACK-012](docs/specs/SPEC-BACK-012_METAS_SEMANAIS.md).
A coleção Postman inclui a pasta `Weekly Goals` e propaga `goal_id`/`goal_version`.

## Requisitos de ambiente

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

## Reconciliação periódica das filas

Filas ativas são reconciliadas por um comando idempotente que preserva o isolamento por
grupo, saneia itens estrangeiros legados e promove atividades premium vigentes somente
dentro das filas elegíveis. A fila `Todos` permanece agregadora, e execuções iniciadas não
são interrompidas:

```bash
poetry run python manage.py reconcile_premium_queues
poetry run python manage.py reconcile_premium_queues --dry-run
```

Configure a infraestrutura para executá-lo a cada 15 minutos. Esse também é o atraso
máximo esperado quando uma vigência futura passa a valer apenas pela passagem do tempo.
Exemplo de entrada no `crontab`, ajustando o diretório para a instalação real:

```cron
*/15 * * * * cd /srv/backend_pomodoro_task && poetry run python manage.py reconcile_premium_queues
```

Cada fila é processada em sua própria transação e em ordem de identificador. Execuções
sobrepostas são serializadas pelo bloqueio da fila no PostgreSQL; falhas isoladas não
interrompem as filas seguintes e fazem o comando terminar com status diferente de zero.

## Importação de jogos da Steam

Configure no ambiente do servidor:

```env
STEAM_API_KEY=
STEAM_ID64=76561198065747727
STEAM_ACTIVITY_CATEGORY_ID=21
STEAM_ACTIVITY_DEFAULT_DURATION=60
```

A chave é obrigatória e não deve ser versionada. Com a categoria configurada previamente
cadastrada, um administrador com permissões de adicionar e alterar atividades pode acessar
`Admin > Activities` e usar o botão **Importar jogos da Steam**. A operação é executada por
`POST`, identifica jogos pelo AppID e não sobrescreve duração, prioridade, estado ou dados de
execução em sincronizações posteriores.

Detalhes técnicos e operacionais estão em
[`docs/specs/IMPORTACAO_ATIVIDADES_STEAM_ADMIN.md`](docs/specs/IMPORTACAO_ATIVIDADES_STEAM_ADMIN.md).

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
docker compose -f compose.yml run --rm backend python manage.py reconcile_premium_queues --dry-run
docker compose -f compose.yml run --rm backend python manage.py reconcile_premium_queues
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

## Premium por período (SPEC-014)

O backend preserva renovações em `PremiumPeriod`, permite sessões diretas com duração de 1 a 720 minutos e calcula tempo premium pela interseção exata entre sessão, vigência e consulta. Sessões `premium_direct` entram no histórico e nas metas, mas ficam fora das cotas operacionais da fila. O catálogo é compartilhado; execuções, configurações e relatórios usam o escopo derivado da chave da API.

Novos inícios ficam desabilitados por padrão. Depois de publicar um cliente que aceite `queue_item_id: null`, habilite:

```env
PREMIUM_DIRECT_START_ENABLED=True
```

Antes da liberação, execute no ambiente de destino, primeiro em simulação:

```bash
python manage.py migrate --noinput
python manage.py import_legacy_premium_periods --dry-run --kind focus
python manage.py import_legacy_premium_periods --kind focus
python manage.py enrich_goal_completion_facts --dry-run
python manage.py enrich_goal_completion_facts
```

Classifique períodos importados como `paid` quando aplicável. O importador usa `focus` por segurança, pois o legado não registra a natureza do período. Os contratos completos estão em `docs/contracts/spec-014-premium.json` e na coleção Postman.

O teste de locks requer uma instância PostgreSQL descartável cujo banco termine em `_test`:

```bash
TESTING=true TEST_POSTGRES_DB=pomodoro_spec014_test \
TEST_POSTGRES_USER=postgres TEST_POSTGRES_PASSWORD=postgres \
python manage.py test apps.pomodoro.test_spec_014_postgres \
  --settings=config.settings.postgres_concurrency
```
