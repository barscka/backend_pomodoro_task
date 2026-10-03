# SPEC-017 — Contrato backend v1

Todas as rotas abaixo usam `/api/` (o mesmo prefixo dos contratos existentes),
Authorization `Api-Key ...`, HasAPIKey e escopo derivado no servidor. Não enviar
scope_key. GETs não reconciliam sessões nem criam/apresentam filas.

- `GET routines/`: plano, versão global e revisões imutáveis; estado `no_plan`.
- `POST routines/`: `{expected_version: 0, effective_from: YYYY-MM-DD, blocks: [...]}`.
- `PATCH routines/`: mesmo corpo, versão atual; substituição completa dos blocos.
- `POST routines/template/`: `{expected_version: 0, effective_from: YYYY-MM-DD}`;
  aplicação explícita, uma vez por escopo, retries retornam o plano sem revisão nova.
- `PUT routines/exception/`: `{expected_version, origin_date, block_id, action,
  replacement?}`; action cancel/move/shorten/replace. Replacement é um bloco sem ID
  ou weekday, com starts_on opcional (até um dia antes/depois da origem).
- `PUT routines/suspension/`: `{expected_version, date, suspended: true|false}`.
- `GET routines/agenda/?date_from=...&date_to=...`: dias civis inclusivos, máximo
  31 dias. Ocorrências têm fim exclusivo e incluem a véspera quando cruza o início.
- `GET routines/context/`: Agora do servidor, próximo bloco, próximo gameplay,
  tempo restante; estados no_plan/suspended/free/gameplay/cardio/family.
- `GET|PUT routines/activity-preference/?activity_id=...`: classificação opcional;
  PUT recebe `{activity_id, expected_version, pause_immediately, online,
  requires_group, competitive, focus_suitable, minimum_minutes}`. Booleanos null
  significam desconhecido; não são inferidos.
- `GET routines/recommendations/`: até 100 atividades ativas classificadas como
  recommended no contexto atual; `?page=...` pagina determinística por ID.
- `GET|PUT routines/selection/`: identidade `{origin_date, block_id}`; PUT inclui
  expected_version global do plano e source premium/queue, premium_period_id ou
  group_id, return_group_id opcional. Não armazena item futuro.
- `GET routines/preview/?origin_date=...&block_id=...`: preferência persistida,
  item canônico, suitability e motivos, disponibilidade, suggested_duration_minutes,
  término se iniciado agora, crosses_next_block, token assinado (15 minutos).
- `POST routines/start/`: `{origin_date, block_id, preview_token, request_id,
  duration_minutes?}`. Revalida snapshot sob locks; conflito stale_preview retorna
  preview atualizado. Usa exclusivamente premium_direct/queue e timer existente.
  Duração Premium 1–720; fila mantém Activity.duration. Idempotência por request_id.
- `GET routines/summary/?date_from=...&date_to=...`: dias civis, confirmado dentro,
  fora, interrompível, sem cobertura e estimativa aberta separada.
- `PATCH gameplay-tracking-settings/`: campo opcional reference_source
  fixed_daily/routine, padrão fixed_daily (300 min/dia preservado).

Bloco: `{block_id: UUID, weekday: 0..6, kind: gameplay|cardio|family,
profile: general|focus|interruptible, start_time: HH:MM, end_time: HH:MM,
end_day_offset: 0|1}`. Máximo 64 blocos/revisão. Revisão inicial começa hoje ou
futuramente; edições começam no mínimo amanhã e após a última revisão agendada.
Versão global cobre revisões, exceções, suspensão e seleção. Exceções e seleção
não editam datas passadas; ocorrência noturna ainda ativa aceita seleção na madrugada.
Suspensão de dia civil remove orientação inclusive da parte da ocorrência da véspera;
a agenda mantém identidade/data de origem e recorta intervalos suspensos.

Casual exige pause_immediately=true, online=false, requires_group=false,
competitive=false. Incompatibilidade explícita prevalece sobre desconhecido.
Foco exige focus_suitable=true. Duração mínima maior que janela é inadequada.
Perfil geral sem mínimo conhecido informa insufficient_classification. Suitability
não bloqueia início. Família/cardio/livre/suspensão não geram recomendação proativa.

Erros `{code, detail, ...}`: invalid_routine (400), invalid_interval (400),
not_found (404), stale_routine_version (409), overlap (409), stale_preview (409),
source_unavailable (409), além dos conflitos canônicos de execução. Não há início
por horário, apresentação de fila ou criação de revisão de pulados implícitos.

Referência routine soma intervalos civis sem multiplicar por jogos; denominadores
separados general_seconds/interruptible_seconds. Sem planejamento ou suspensão
retorna referência desconhecida (`reference_seconds: null`, coverage partial).
Resumo usa grupos explicitamente configurados no acompanhamento, origens diretas
Premium/Retro e sessões de fila com período Premium; não classifica estudo como jogo.
Legado impreciso é contado como cobertura insuficiente, sem inventar intervalos.

## Detalhes entregues e decisões

- Datas aceitas na API de rotina: 2000–2100, evitando overflow na expansão da véspera.
- `expected_min_minutes`/`expected_max_minutes` opcionais e null nos blocos;
  só cardio aceita expectativas. Modelo inicial informa 15/30, sem registrar treino.
- Cada revisão retorna `nominal_capacity`: gameplay/general/interruptible/family/
  cardio em segundos e gameplay_seconds_by_weekday (segunda=0). Base é a semana
  por dia de origem. O resumo divide por dia civil: na primeira semana da primeira
  vigência pode faltar a cauda da véspera, enquanto o bloco de domingo termina
  fora do intervalo. Não retroagimos planejamento para fechar artificialmente 42h.
- `availability` da prévia é a possibilidade de iniciar **agora**; para Premium,
  `available_at_occurrence_start` informa vigência no início planejado futuro.
  Isso é previsão sem reserva e deve ser revalidado na ação explícita de início.
- Contexto atual identifica `current`, `next_block`, `next_gameplay`,
  `remaining_seconds` e `as_of`. Todos os instantes incluem offset do fuso.
- Lista `days` da agenda diferencia planned/no_plan/suspended; espaços entre
  ocorrências de um dia planejado são free no contexto. Famílias/cardio seguem
  acessíveis nos módulos gerais, sem uma permissão nova de execução.
- `queue.mode=skipped_review` e `skip_locked=true` identificam a revisão existente.
  Review bloqueada/vazia nunca cria outra fila. `reason_code` identifica uma
  inelegibilidade canônica e `reason` traz explicação legível.
- O token assinado inclui escopo, revisão/seleção, identidade do item/período,
  classificação e contexto atual. Não é autorização independente da API key.
  Request UUID e payload são persistidos separadamente para replay após conclusão;
  não substituem as garantias de idempotência dos serviços existentes.
- No início são adquiridos os mesmos locks de item/período e relações usados pelos
  serviços canônicos, além do plano. Fila ainda pending só é apresentada/consumida
  quando a ação explícita de início chama start_activity.
- Campos da classificação omitidos em PUT preservam o valor existente; null apaga
  a classificação. `minimum_minutes` é opcional na recomendação casual, pois a
  compatibilidade explícita dos quatro booleanos basta nesse perfil.
- PATCH parcial das configurações preserva group_ids e minutos quando omitidos;
  group_ids=[] limpa a seleção explicitamente. Códigos anteriores
  expected_version_required/invalid_daily_reference/stale_settings_version continuam.
- Cada jogo Premium recebe o mesmo denominador do intervalo (não multiplicado por
  quantidade de jogos). Referência de período mantém a convenção de dias civis
  decorridos incluindo o dia atual, recortada nos limites da vigência Premium.
- Não há gravação de metas, dívida, exercício ou atenção familiar na rotina.
  Facts de conclusão continuam no fluxo canônico. Fact impreciso ou execução
  concluída do escopo sem fact contribui para insufficient_precision_count,
  sem fabricar tempo confirmado. Histórico sem escopo não é atribuído à chave atual.

## Fixtures para Flutter

[spec-017-routines.json](spec-017-routines.json) contém 17 respostas HTTP reais,
versionadas e verificadas por teste golden. IDs numéricos são normalizados para
exemplos (activity 101, group 201, Premium 301, queue 401, item 501); o token é um
placeholder. Solicite uma prévia real antes de iniciar. Não copie o token da fixture.

Para regenerar deliberadamente, em ambiente de teste isolado:

```sh
UPDATE_ROUTINE_FIXTURES=1 APP_ENV=test \
TEST_DATABASE_URL=sqlite:///tests/.tmp/routines.sqlite \
DJANGO_SETTINGS_MODULE=config.settings.test .venv/bin/python manage.py test \
apps.pomodoro.test_spec_017.RoutineTests.test_flutter_contract_fixtures --noinput
```

Fluxo sugerido: ler plano/contexto → escolher ocorrência pelo block_id/origin_date →
salvar fonte com expected_version → consultar preview → iniciar com preview_token,
request_id e duração explícita se Premium → recuperar timer pelo contrato existente.
Em 409 stale_preview, substituir a prévia exibida; requerer nova ação de início,
sem escolher automaticamente outra atividade. Em stale_routine_version, reler o plano.
