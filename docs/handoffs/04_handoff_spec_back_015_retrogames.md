# Handoff — SPEC-BACK-015 RetroGames

Data: 2026-09-25

## Entrega

Implementação integral do backend do catálogo e das sessões RetroGames. O domínio
reutiliza `Activity`, `Schedule`, `History`, `GoalCompletion`, o timer canônico e a
idempotência já introduzidos pela SPEC-014.

## Arquivos principais

- `apps/pomodoro/models.py`: identidade do grupo, ordem de geração, modelos RetroGames,
  origem `retro_direct`, FK de jogo e constraints.
- `apps/pomodoro/migrations/0020_retrogame_retrogameprogress_retroplatform_and_more.py`:
  schema aditivo e carga idempotente do grupo `Retrogames`.
- `apps/pomodoro/services/direct_execution.py`: hash canônico compartilhado.
- `apps/pomodoro/services/retro_catalog.py`: consultas, filtros, ordem e métricas.
- `apps/pomodoro/services/retro_progress.py`: transições otimistas por versão.
- `apps/pomodoro/services/retro_execution.py`: início/continuação diretos e idempotentes.
- `apps/pomodoro/serializers.py`, `views.py` e `urls.py`: contratos HTTP finos.
- `apps/pomodoro/admin.py`: cadastros, filtros, buscas e validações editoriais.
- `apps/pomodoro/test_spec_015.py`: testes isolados de modelo, Admin, API e integração.
- `apps/pomodoro/test_spec_015_postgres.py`: concorrência real em PostgreSQL descartável.
- `docs/contracts/spec-015-retrogames.json`: contrato frontend versionado.

## Endpoints

- `GET /api/retro-generations/`
- `GET /api/retro-platforms/?generation_id=<id>`
- `GET /api/retro-games/`
- `GET /api/retro-games/<id>/`
- `GET /api/retro-games/<id>/sessions/`
- `POST /api/retro-games/<id>/start/`
- `PATCH /api/retro-games/<id>/progress/`
- `POST /api/activity-executions/<id>/continue/` também aceita `retro_direct`.

Listas de jogos e sessões usam paginação por página, padrão 30 e máximo 100. Filtros
de jogos: `generation_id`, `platform_id`, `tier`, `status`, `search` e `active`.

## Decisões e compatibilidade

- `scope_key` continua derivado da API key e nunca aparece em request/response.
- A flag `RETROGAMES_ENABLED` fica desligada por padrão e não bloqueia leitura.
- Início/continuação não cria item de fila, período Premium ou evento de preferência.
- Conclusão permanece no serviço comum, garantindo History e GoalCompletion idempotentes.
- Progresso editorial não é concluído automaticamente por tempo e sessões novas não
  reabrem estados `completed` ou `skipped`.
- Consultas de elegibilidade da fila foram corrigidas para considerar apenas origens
  `queue`/`legacy`, preservando a decisão já documentada para Premium e estendendo-a a
  RetroGames.
- O callable de categoria padrão passou a consultar somente a PK ao reproduzir schema
  histórico; isso permite criar banco de testes do zero sem editar migrations antigas.

## Validações executadas

```text
python manage.py check                                      OK
python manage.py makemigrations --check                    OK
python manage.py test apps.pomodoro.test_spec_015          15 OK
python manage.py test apps.pomodoro.test_spec_014           6 OK
python manage.py test                                      174 OK, 5 skipped
python -m compileall -q apps config                        OK
python -m json.tool docs/contracts/spec-015-retrogames.json OK
git diff --check                                            OK
```

Todos os comandos Django foram executados com `DJANGO_SETTINGS_MODULE=config.settings.test`,
`APP_ENV=test` e `TESTING=true`, usando SQLite descartável criado pelo runner.

## Riscos e deploy

- O teste PostgreSQL de concorrência existe, mas não foi executado: o ambiente não tinha
  `TESTING=true` e credenciais `TEST_POSTGRES_*` para banco com sufixo `_test`.
- Aplicar a migration `0020` antes de cadastrar plataformas/jogos.
- Publicar frontend compatível com `retro_direct` e `retro_game_id` antes de ativar
  `RETROGAMES_ENABLED=True` nos dispositivos que compartilham a mesma API key.
- O roteiro curado e as capas continuam dependentes de cadastro editorial no Admin;
  nenhuma ROM, imagem ou integração com emulador foi importada.
