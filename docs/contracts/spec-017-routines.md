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

## Sessões associadas automaticamente — extensão aditiva de 2026-10-04

Migration 0022. Inícios canônicos queue, premium_direct e retro_direct não exigem
origin_date/block_id do cliente. O servidor associa uma **nova** sessão ao gameplay
ativo em Schedule.starts_at, com fuso da rotina, revisão vigente no dia de origem,
exceções, suspensão civil e intervalo `[starts_at, ends_at)`. Às 00h15, um bloco
22h30–01h mantém a origem na véspera; às 01h00 já não há vínculo com esse bloco.
Sem gameplay ativo, a execução inicia normalmente e routine_occurrence é null.
Fila comum é gameplay apenas pelos grupos configurados, ou período Premium da
sessão; não é classificada pelo nome do grupo ou pelo uso da tela Foco.

A resposta de execução, incluindo recuperação, ganha:

```json
{
  "routine_occurrence": {
    "occurrence_id": "2026-10-10:2d2bcb15-32aa-4ed5-8c0b-a68a0f050327",
    "block_id": "2d2bcb15-32aa-4ed5-8c0b-a68a0f050327",
    "origin_date": "2026-10-10",
    "revision_version": 1,
    "timezone": "America/Sao_Paulo",
    "kind": "gameplay",
    "profile": "general",
    "starts_at": "2026-10-10T22:30:00-03:00",
    "ends_at": "2026-10-11T01:00:00-03:00"
  },
  "routine_requested_occurrence": null
}
```

routine_requested_occurrence é a identidade da preferência enviada a routines/start/,
com occurrence_id/block_id/origin_date. Ela pode diferir do bloco ativo, ou existir
sem associação automática (início fora da janela ou fila não classificada). Inícios
pelos módulos diretos retornam null nesse campo. Preferência não determina contagem.

GET agenda/ aceita adicionalmente `sessions_page=1` (1–1.000.000). Cada ocorrência
atual inclui sessions, sessions_pagination e session_totals. A raiz inclui as_of e
recorded_occurrences. Essa segunda lista contém os vínculos históricos mesmo quando
um ajuste cancelou/moveu a ocorrência atual ou o plano foi removido. Os campos
anteriores da agenda/contexto/resumo permanecem disponíveis.

Forma de cada recorded_occurrences:

```json
{
  "occurrence_id": "2026-10-10:2d2bcb15-32aa-4ed5-8c0b-a68a0f050327",
  "block_id": "2d2bcb15-32aa-4ed5-8c0b-a68a0f050327",
  "origin_date": "2026-10-10",
  "snapshots": [{"occurrence_id": "...", "revision_version": 1, "starts_at": "2026-10-10T22:30:00-03:00", "ends_at": "2026-10-11T01:00:00-03:00"}],
  "sessions": [{
    "execution_id": 601,
    "activity_id": 101,
    "activity_name": "Jogo manual",
    "execution_origin": "premium_direct",
    "state": "completed",
    "starts_at": "2026-10-10T23:37:00-03:00",
    "ends_at": "2026-10-11T01:37:00-03:00",
    "expected_end_at": "2026-10-11T01:37:00-03:00",
    "confirmed_seconds": 7200,
    "open_estimate_seconds": 0,
    "coverage": "complete",
    "routine_occurrence": {"occurrence_id": "...", "origin_date": "2026-10-10"},
    "routine_requested_occurrence": null
  }],
  "sessions_pagination": {"page": 1, "page_size": 50, "count": 1, "has_next": false},
  "session_totals": {"confirmed_seconds": 7200, "open_estimate_seconds": 0}
}
```

Os objetos abreviados acima estão completos nas
[fixtures de sessões](spec-017-routine-sessions.json). Há cinco exemplos verificados,
incluindo preferência diferente do bloco real. Fixtures originais atualizadas.
IDs são ilustrativos/normalizados; instantes e identidades de bloco são dados de teste.

Semântica:
- Uma associação única por execução, gravada na mesma transação de início.
- Retries retornam o vínculo original; sessões antigas ou inicialmente sem vínculo
  nunca ganham associação por GET, conclusão ou retry. Não há backfill nesta migration.
- Snapshot independente do plano e catálogo: data, ID, revisão, fuso, intervalos,
  perfil, atividade e origem não são recalculados após edições futuras.
- snapshots pode conter mais de um intervalo para o mesmo occurrence_id se ajustes
  ocorreram entre sessões do mesmo dia. Cada sessão informa seu snapshot específico.
- confirmed_seconds vem exclusivamente do fact canônico de conclusão com precisão.
  É o total real da sessão vinculada, incluindo eventual ultrapassagem do bloco.
  Não é recortado ao planejamento nem comprova cumprimento integral do bloco.
- open_estimate_seconds é estimativa limitada ao as_of/término esperado. GET não
  conclui a sessão atrasada; tempo estimado nunca entra no confirmado.
- Cancelada/expirada ou registro sem fact preciso não fabrica tempo confirmado;
  coverage pode ser insufficient. Sem Schedule nem fact, state é unavailable.
- Uma associação continua disponível após exclusão do plano e após exclusão de
  Schedule; se existir GoalCompletion preciso, seu tempo confirmado é preservado.
- Cada página tem até 50 sessões por ocorrência. count e session_totals abrangem
  todas as sessões, mesmo quando a página está vazia. Ordenação: início, execution_id.
- Consulta inclui ocorrências cujos snapshots cruzam o intervalo civil **ou** cuja
  data de origem está no intervalo; detalhes de sessão não são recortados por dia.
- sessions nas occurrences e recorded_occurrences representam os mesmos vínculos.
  Não somar ambas as listas; deduplicar por execution_id e unir por occurrence_id.
- Resumo civil permanece calculado pelos facts/interseções. No exemplo de 23h37 a
  01h37, a agenda mostra 7200s na ocorrência de sábado; resumo registra 23min dentro
  no sábado, 60min dentro no domingo e 37min fora. Não adicionar session_totals ao resumo.

Para Flutter: renderizar sessões nos cards usando occurrence_id; usar snapshots para
ocorrências históricas ausentes da lista atual ou com horários alterados. Não mover
visualmente o registro para um intervalo editado só porque o ID continua igual.
Atualizar a agenda após início,
conclusão/reconciliação canônica e recuperação, mantendo o timer único. Enquanto
aberta, atualizar estimativa conforme as_of ou reler a agenda; não alterar a identidade
pelo horário local. Carregar páginas adicionais com sessions_page, deduplicando IDs.
A agenda não precisa chamar activities/next nem enviar bloco em inícios diretos.
