# Handoff — backend de rotinas flexíveis (SPEC-017)

Entrega: 2026-10-03, branch `codex/plano-rotinas-flexiveis`, a partir dos documentos
nos commits a0c55b6/b9801de. Backend implementado; Flutter e implantação pendentes.
Não houve acesso a banco real, aplicação de migration real, carga ou mudança de flag.

## Padrões carregados

Doctor passou, com aviso de PERSONAL_SKILLS_HOME não definida; local_path resolveu
`/home/barscka/workspace/skills/skills_pessoais`.
Lidos AGENTS.md, .personal-skills.json e configuração local.
Carregados personal-dev-workflow (Git, commits e engenharia_ia) e personal-python-api,
com todas as referências requeridas no manifesto: django, fastapi, flask, poetry,
python_api, python_database_safety, python_project_structure, python_tests e
python_upgrade. Não foram copiadas skills para o projeto.

## Arquivos e responsabilidades

- `apps/pomodoro/models.py`: plano/revisões/blocos/exceções/suspensões,
  classificação por escopo, seleção por ocorrência, replay de início e opção de
  referência nas configurações de gameplay.
- `apps/pomodoro/migrations/0021_routineplan_and_more.py`: migration aditiva,
  constraints e índices implícitos dos FKs/unique; sem RunPython ou seed.
- `apps/pomodoro/routine_serializers.py`: contratos estritos e validação de entrada.
- `apps/pomodoro/routine_views.py`, `urls.py`: transporte fino e rotas HasAPIKey.
- `apps/pomodoro/repositories/routines.py`: consultas somente leitura do plano,
  execução aberta e item canônico, sem chamar present_next_item/get_active_schedule.
- `apps/pomodoro/services/routines.py`: revisões, agenda, exceções, suspensão,
  contexto, perfis, adequação, recomendação e seleção.
- `apps/pomodoro/services/routine_selection.py`: token assinado, prévia sem mutação,
  locks, revalidação, replay e delegação para start_activity/start_premium.
- `apps/pomodoro/services/routine_reporting.py`: capacidade civil, cruzamento
  temporal e cobertura insuficiente; estimativa aberta separada.
- `apps/pomodoro/services/gameplay_settings.py`, `premium_reporting.py`, `views.py`:
  referência opt-in, denominadores e edição concorrente das configurações.
- `apps/pomodoro/test_spec_017.py`, `test_spec_017_postgres.py`: contratos, domínio,
  golden fixtures e concorrência.
- `apps/pomodoro/test_spec_015_postgres.py`: preparação do grupo Retro independente
  do seed da migration, que TransactionTestCase apaga no flush entre casos.
- `docs/contracts/spec-017-routines.md`, `spec-017-routines.json`: contrato v1,
  decisões técnicas e 17 exemplos HTTP normalizados para Flutter.
- `docs/postman/backend_pomodoro_task.postman_collection.json` e environment:
  pasta de rotinas e variáveis vazias, sem credenciais.
- `README.md`, SPEC-017, roadmap e este handoff: estado efetivamente entregue.

## Contratos para implementação Flutter

Prefixo `/api/routines/`. Authorization da chave atual; não enviar scope_key.

| Necessidade | Rotas principais |
| --- | --- |
| Plano e modelo inicial | GET/POST/PATCH routines/; POST routines/template/ |
| Ajustes locais | PUT exception/ e suspension/ |
| Agenda/contexto | GET agenda/ (1–31 dias inclusivos) e context/ |
| Adequação | GET/PUT activity-preference/; GET recommendations/ (100/página) |
| Fonte por ocorrência | GET/PUT selection/ com block_id + origin_date |
| Prévia/início | GET preview/; POST start/ com token + request_id |
| Planejado/registrado | GET summary/ (1–31 dias) |
| Referência Premium | PATCH gameplay-tracking-settings/ com reference_source |

A versão global do plano cobre edições, exceções, suspensão e seleção; classificação
usa versão própria. Ocorrência conserva a data local do início, inclusive à 00h30.
Não reservar queue_item_id no formulário; persistir somente group_id/Premium.
Não chamar activities/next para prévia: essa rota apresenta/cria filas.

Prévia retorna fonte, atividade/período/item, fila/mode/skip_locked, adequação com
reasons.code/detail, duração, sugestão Premium, predicted_end_at, crosses_next_block
 e availability. availability é início agora; available_at_occurrence_start prevê
vigência Premium no bloco futuro. Token vale 15 minutos, preso ao escopo/contexto.

No stale_preview (409), mostrar a prévia/contexto atualizado e esperar nova ação
explícita; não iniciar outro jogo automaticamente. No stale_routine_version, reler
plano. Preservar request_id e corpo no retry do mesmo início. Timer e conclusão
continuam no coordenador canônico, sem uma terceira origem ou outro relógio.

Classificação desconhecida é null, não false. Casual exige pausa imediata, solo,
sem online e sem competitivo explicitamente confirmados. Incompatibilidade impede
recomendação, sem impedir escolha manual/início. Perfil geral não é foco implícito.

Estados no_plan/free/suspended/gameplay/cardio/family são distintos. Disponibilidade
nominal semanal usa dia de origem (42h); resumo civil divide a madrugada e pode
ter cobertura incompleta na primeira semana da vigência, sem retroagir o plano.
Família aparece apenas como reserva, sem percentual de atenção realizada.

## Validação executada

- Doctor: passou.
- `manage.py check`: sem problemas.
- `manage.py makemigrations --check --dry-run`: nenhuma alteração pendente.
- `git diff --check`: passou.
- SQLite isolado: **232 testes**, sucesso, **12 ignorados** exclusivos de locks
  PostgreSQL. 220 passaram, incluindo regressões Premium/RetroGames/fila/metas.
- PostgreSQL 16 descartável: **50 testes**, todos passaram; 38 de rotinas/contratos,
  sete de concorrência das rotinas, Premium/Retro concorrentes e três de metas.
- Fixtures golden verificadas em SQLite e PostgreSQL; JSON/Postman validados.

Comando SQLite:

```sh
APP_ENV=test TEST_DATABASE_URL=sqlite:///tests/.tmp/routines.sqlite \
DJANGO_SETTINGS_MODULE=config.settings.test .venv/bin/python manage.py test --noinput
```

PostgreSQL foi criado exclusivamente para esta task: container
`codex-routines-pg-test`, postgres:16, tmpfs, porta local 55437; credenciais locais
artificiais, sem volume persistente, removido após validação. O perfil exige
TESTING=true e nome terminado em _test. Nunca substituir por banco compartilhado.

```sh
TESTING=true TEST_POSTGRES_DB=routines_test TEST_POSTGRES_USER=routine_test \
TEST_POSTGRES_PASSWORD=isolated_test_only TEST_POSTGRES_HOST=127.0.0.1 \
TEST_POSTGRES_PORT=55437 DJANGO_SETTINGS_MODULE=config.settings.postgres_concurrency \
.venv/bin/python manage.py test apps.pomodoro.test_spec_017 \
apps.pomodoro.test_spec_017_postgres apps.pomodoro.test_spec_014_postgres \
apps.pomodoro.test_spec_015_postgres \
apps.pomodoro.test_weekly_goals.GoalConcurrencyTests --noinput
```

Locks testados: primeira aplicação concorrente idempotente, edição de revisão e
seleção com um vencedor, execução Premium única, replay de fila único e item trocado
antes/durante espera do lock sem iniciar a atividade substituta.

Na validação inicial PostgreSQL, um teste antigo de RetroGames dependia do seed
apagado pelo flush. Corrigida somente sua preparação; as execuções finais passaram.
Logs SQLite de RuntimeError: boom pertencem aos testes existentes de tratamento de
falhas simuladas; não são falhas da suíte.

## Limites e riscos

- Não houve validação de telas Flutter, deploy ou homologação do ambiente real.
- Padrão PREMIUM_DIRECT_START_ENABLED=False preservado; disponibilidade da prévia
  respeita o valor efetivo da flag. Não foi alterada nenhuma flag de ambiente.
- Sessões comuns da fila só entram em gameplay quando seus grupos forem escolhidos
  explicitamente nas configurações; origens diretas e sessões vinculadas a Premium
  entram sem inferir pelo nome do grupo. Retro permanece no módulo existente.
- Sem precisão histórica/fact de conclusão, o resumo informa cobertura insuficiente;
  não estima tempo confirmado nem atribui histórico sem escopo à chave atual.
- Referência routine retorna denominador null se algum dia for desconhecido ou
  suspenso. Não transforma ausência de plano em zero conhecido.
- Sem pausa/retomada: timer continua contando até encerramento/reconciliação.
- Edições semanais são revisões completas, prospectivas e append-only. Não há remoção
  de revisão histórica. Exceção cancela/move/encurta/substitui uma ocorrência.
- Um GET não reconcilia execução vencida: permanece conflito explícito até o fluxo
  canônico de execução realizar sua reconciliação. Isso preserva GET somente leitura.
- PostgreSQL validado localmente em instância descartável; distribuição e volume de
  produção ainda requerem homologação operacional.

## Implantação ainda não executada

1. Revisar/publicar o commit e preparar backup conforme processo do ambiente.
2. Aplicar migration 0021 no destino autorizado e reiniciar o backend.
3. Integrar Flutter usando contratos/fixtures e tratar versões, estados e conflitos.
4. Por ação explícita, aplicar o template com a primeira vigência escolhida por chave.
5. Configurar grupos de gameplay/classificações e, opcionalmente, referência routine.
6. Homologar início, replay, conclusão, madrugada, troca de dispositivo e conflitos
   entre queue, premium_direct e retro_direct; verificar flags existentes no destino.

Nenhum desses passos operacionais foi realizado no banco real nesta entrega.
