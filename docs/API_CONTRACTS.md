# Contratos públicos da API

Referência central e vigente para integração com o frontend. Revisada em **17/10/2026** contra URLs, views, serializers, services, constraints e testes do repositório. Este documento descreve a implementação; inconsistências conhecidas não são promessas de correção já entregue. Exemplos usam IDs fictícios.

## Convenções e acesso

- Base: `/api/`; caminhos com barra final. JSON: `Accept: application/json`, `Content-Type: application/json` nas escritas.
- Todos os endpoints de domínio exigem `Authorization: Api-Key <chave>`. Chave ausente, inválida ou revogada: normalmente **403**, corpo DRF `{"detail":"..."}`. Não há login/token de usuário público. SessionAuthentication padrão do DRF pode exigir CSRF em escritas de sessão autenticada.
- Execuções, filas, metas, preferências e progresso usam escopo derivado da chave (`build_scope_key`); catálogo é compartilhado. Trocar chave muda o escopo. Histórico legado de activities é global: não presumir isolamento em todos os GETs.
- IDs são inteiros JSON; UUIDs são strings. Datas `YYYY-MM-DD`, instantes ISO 8601 com offset; temporização de ActivityExecution usa UTC. Horário civil/semana: `America/Sao_Paulo`.
- `?` nas descrições significa nullable; omissão de entrada e `null` são distintos. Tabelas de serializers ao final definem obrigatoriedade de entrada, aceitação de null e limites. Em PATCH campos omitidos são preservados, salvo regra explícita.
- Listas sem paginação global: groups, categories, activities, retro-generations, retro-platforms. Paginação DRF `{count:int,next:string|null,previous:string|null,results:array}`: premium-periods e weekly-goals (20), retro-games e filmes/coleções/histórico de filmes (30); `page` e `page_size`, máximo 100.
- Erros não têm envelope único: validação DRF pode retornar `{campo:[mensagens]}` ou `non_field_errors`; serviços retornam `{code,detail,...}`; rotas legadas usam `{error}` e algumas respostas 404 são vazias. `code` é estável para decisão do cliente; texto humano pode mudar. Métodos não expostos: 405; JSON inválido: 400; tipo de mídia não aceito: 415; página inexistente: 404.
- HEAD/OPTIONS são disponibilizados pelo framework nas views compatíveis. `/api/` é a raiz do router, não um catálogo de dados.

## Execução: origem, contexto e timer

`execution_origin` identifica **como a sessão começou**, não a exclusividade de toda atribuição analítica. `premium_period_id` registra o período vigente da atividade no início, inclusive quando a sessão veio da fila. `retro_game_id` identifica exclusivamente início retrô direto. Os IDs não são parâmetros intercambiáveis.

| Origem | queue_item_id | premium_period_id | retro_game_id | Situação |
|---|---|---|---|---|
| queue | inteiro | null | null | fila comum |
| queue | inteiro | inteiro | null | **válida: fila durante período premium** |
| premium_direct | null | inteiro | null | premium direto |
| retro_direct | null | null | inteiro | retrô direto |
| legacy | null em registros sem item | null em registros sem atribuição | null | legado; tratar como registro histórico, não inventar contexto |

Demais combinações não são produzidas pelos serviços canônicos e não devem ser fabricadas pelo cliente. A constraint `schedule_retro_origin_context` proíbe retro com fila/premium e retro_game em outras origens. As demais invariantes são dos serviços: o banco não impõe toda a matriz; registros antigos/inserções administrativas exigem auditoria própria. FKs premium/retro são protegidas contra exclusão. Não impor a um registro histórico todas as garantias de uma nova sessão.

### Divergência confirmada com Flutter

`services/activity_execution.py:start_activity` consulta `active_period_for_activity(activity, at=now)` e grava, na mesma Schedule, `execution_origin=queue`, `queue_item` e `premium_period`. `ActivityExecutionSerializer` expõe esses valores sem transformar a origem. Logo `/api/activities/active/` com `execution_origin="queue"` e `premium_period_id=2` é **coerente**, desde que tenha o item da fila e retro_game_id null. O número 2 isoladamente não permite confirmar a vigência de um registro real; não houve consulta ao banco operacional nesta revisão.

No frontend inspecionado, `lib/models/activity_execution.dart:ActivityExecution.fromJson` define `queueHasDirectContext` como queue com premium **ou** retro, lançando `Contextos de execucao incompatíveis`. Esse predicado confunde atribuição premium com origem direta. Correção necessária no cliente: permitir queue+premium, preservar a proibição queue+retro e manter as exigências de IDs das origens diretas. Aplicar o mesmo parser à recuperação, respostas de início e `active_execution` dos conflitos; testar também persistência/restauração do snapshot. Não apagar premium_period_id nem trocar origem para contornar o parser: isso perde atribuição ou muda cotas/continuação. Nenhuma regra ou código do frontend foi alterado nesta entrega.

### Resposta Execution

Todos os campos de `ActivityExecutionSerializer` constam no apêndice. `id=execution_id` identifica Schedule; `activity.id` identifica Activity, `queue_item_id` identifica item. `queue_id`, `queue_group_id`, `queue_group_name`, `queue_mode`, `skip_locked` e métricas de grupo são null sem fila. O grupo da atividade pode diferir do grupo da fila default; frontend deve usar `queue_group_*` para navegação da fila.

`state`: preparing, running, completed, cancelled, expired. Nova sessão começa running, version=1. Só há uma preparing/running por escopo não vazio. `requested_at`, `starts_at`, `expected_end_at`, `completed_at` podem ser null no legado; novas sessões têm os três primeiros. `planned_duration_seconds` é inteiro nullable no legado, minutos × 60 nas novas. `remaining_seconds=max(int(expected_end_at-now),0)` ou 0 sem término; `server_now` ancora o relógio do cliente. Não calcular duração por subtração de horários civis ou pela duração atual do catálogo.

`continued_from_id` e `return_group_id`: inteiro|null; grupo de retorno é contexto de navegação, não vínculo à fila. `routine_occurrence`: objeto|null com occurrence_id, block_id, origin_date, revision_version, timezone, kind, profile, starts_at, ends_at; snapshot do bloco ativo em starts_at. `routine_requested_occurrence`: objeto|null com identidade solicitada na prévia; pode diferir do bloco real. Nunca usar preferência como tempo confirmado.

GET active/status/retrieve e reconcile podem concluir sessão vencida. A conclusão é idempotente, incrementa versão uma vez, registra fact/histórico e consome item quando há fila. Conclusão antecipada limita término a min(now,expected_end_at); cancelled/expired não são concluíveis. Sessões diretas entram em histórico/metas e não consomem cotas operacionais da fila.

### Operações de execução e fila

Todos os caminhos abaixo começam com `/api/`.

| Método e caminho | Entrada | Sucesso e regras |
|---|---|---|
| GET activities/next/ | query group_id opcional | 200 QueueItem; apresenta item e pode criar/reconciliar fila; 404 no_activity_available com reason e métricas |
| POST activities/{id}/start/ | body queue_item_id obrigatório | 201 Execution; 200 retry do mesmo item aberto. Acrescenta schedule_id,activity_id,date,start_time,status=started/already_started |
| GET activities/active/ | nenhuma | 200 Execution ou **204 sem corpo** |
| GET activities/status/{schedule_id}/ | ID do escopo | 200 Execution + schedule_id; 404 `{error}` |
| POST activities/complete/ | schedule_id obrigatório | 200 Execution + schedule_id,status=completed; 400 `{error}` ausente; 404 `{error}` não encontrado; 409 execution_not_completable |
| GET activity-executions/{id}/ | ID do escopo | 200 Execution ou 404 vazio; reconcilia |
| POST activity-executions/{id}/reconcile/ | sem campos obrigatórios | mesmo contrato de retrieve |
| POST activity-executions/{id}/continue/ | PremiumContinueSerializer | 201 nova Execution ou 200 replay; exige predecessora completed na expected_version e mesmo contexto; uma sucessora |
| POST activity-queue/items/{id}/skip/ | sem campos obrigatórios | 200 `{queue_item_id:int,activity_id:int,state:string,next_queue_item_id:int|null}`; pode haver bloqueio de skip na revisão |
| GET activity-queue/activities/ | query group_id opcional | 200 QueueActivityListResponse; até 30 itens; statistics_scope=all_time; não cria fila |
| POST activity-queue/recreate/ | expected_queue_id obrigatório; group_id opcional | 201 QueueRecreationResponse; comparação otimista, reenvia pulados elegíveis |

Sem group_id, seleção usa grupo default; grupo default agrega atividades elegíveis de outros grupos, enquanto grupos específicos isolam seu catálogo. Fila premium prioriza vigência; expiração reconcilia elegibilidade. Não escolher início pelo ID de Activity sem antes obter o item apresentado.

Erros: início sem item 400 queue_item_required; item inexistente 404 queue_item_not_found. Conflitos de serviço de início/skip: 409 com code/detail/contexto (catálogo no apêndice). Exceção inesperada capturada: 500 activity_start_failed, queue_item_skip_failed ou queue_recreation_failed. Recreate: 400 expected_queue_id_required/invalid_request; 404 group_not_found/active_queue_not_found; outros conflitos 409. Listagem de fila valida group_id (400 invalid_request, 404 group_not_found).

**active_execution é opcional**, presente quando ActivityExecutionConflict carrega Schedule nos inícios de activities, premium-periods, retro-games e continue. Seu valor é a **Execution completa**, com a mesma matriz de contextos, não QueueItem nem booleano. Não presumir presença em todo 409. A view de routines/start atualmente descarta exc.schedule e não inclui active_execution: inconsistência de transporte a corrigir separadamente se o cliente precisar recuperar diretamente pelo conflito; pode consultar active/. Continue também descarta exc.payload.

## Catálogo de atividades

| Método e caminho | Parâmetros/corpo | Resposta |
|---|---|---|
| GET groups/; GET groups/{id}/ | nenhum | array Group / Group; 404 detalhe |
| GET categories/; GET categories/{id}/ | nenhum | array Category / Category; 404 detalhe |
| GET activities/ | category_id,group_id opcionais | array Activity; só ativas e sem filme; ordena -premium,name |
| POST activities/ | Activity (campos graváveis no apêndice) | 201 Activity; 400 validação |
| GET activities/{id}/ | category_id/group_id também filtram queryset | 200 Activity ou 404 |
| PUT/PATCH activities/{id}/ | Activity completo/parcial | 200 Activity; 400 validação |
| DELETE activities/{id}/ | ID | 204; relações protegidas podem impedir exclusão |
| GET activities/history/ | nenhum filtro implementado | array History global em -start_time; 404 detail/suggestion se vazio |

Atividade premium legada exige premium_from e premium_until e início ≤ fim; criação sincroniza período kind=focus/source=legacy_compat. Com períodos já existentes, editar premium/premium_from/premium_until é rejeitado; usar premium-periods. Categoria de atividade vinculada a filmes deve permanecer compatível com todas as coleções. can_execute/remaining_executions dependem do grupo selecionado; não substituem a validação transacional do início.

## Premium, relatórios e configurações

| Método e caminho | Entrada | Saída/status |
|---|---|---|
| GET premium-periods/; GET premium-periods/{id}/ | query activity_id,status=future/active/ended; paginação | 200 página PremiumPeriod / objeto; status desconhecido não filtra |
| POST premium-periods/ | PremiumPeriod gravável | 201 objeto; 400 validação; 409 sobreposição |
| PATCH premium-periods/{id}/ | expected_version + campos | 200 objeto; 409 stale_period_version/premium_period_edit_locked |
| POST premium-periods/{id}/end/ | sem campos obrigatórios | 200 objeto; idempotente; 409 premium_period_not_started para futuro |
| POST premium-periods/{id}/start/ | PremiumStartSerializer | 201/200 Execution; conflitos **409**, inclusive premium_direct_disabled |
| GET premium-periods/{id}/stats/ | nenhum | 200 estatísticas do período |
| GET premium-analytics/daily/ | date_from/date_to opcionais; padrão últimos 30 dias | 200 intervalo/config/as_of/results |
| GET premium-analytics/summary/ | mesmo intervalo | 200 totais/distribution/coverage |
| GET premium-analytics/history/ | activity_id,period_id,origin,limit (1–100),offset≥0 | 200 `{count,results}` History + execution_id,origin,duration_seconds,premium_period_id?; 400 invalid_pagination |
| GET/PATCH gameplay-tracking-settings/ | PATCH TrackingSettingsSerializer | 200 configuração; 400 expected_version_required/invalid_daily_reference/invalid_settings; 409 stale_settings_version |

Período: kind=paid/focus, datas inclusivas em America/Sao_Paulo, limite final exclusivo à meia-noite após ends_on, ou ended_early_at. Sem sobreposição para mesma Activity. Edição só antes de início e sem sessões. Novos inícios diretos exigem PREMIUM_DIRECT_START_ENABLED=True; 1–720 minutos, UUID de request_id obrigatório, replay retorna mesma Schedule; UUID reutilizado com payload diferente gera idempotency_payload_conflict. Continue pode partir de fila com período premium: a sucessora é premium_direct, sem fila. Premium aceita return_group_id nullable; a validação de existência é mais fraca que a de Retro (ver pendências).

Configuração: `{daily_reference_minutes:int (padrão 300),reference_source:fixed_daily|routine,group_ids:int[],version:int (inicial 0),timezone:string}`. PATCH omitido preserva; [] limpa grupos. Relatórios usam interseção exata sessão × período × janela civil, inclusive sessões originadas da fila. Estimativa aberta não entra no consolidado. Intervalo invertido ou diferença >365 dias: 400 invalid_date_range. Percentuais de referência são strings decimais ou null quando denominador indisponível/zero.

Stats: period_id,activity_id,consolidated_seconds,session_count,days_with_gameplay,elapsed_days,daily_reference_minutes,open_estimate_seconds,pending_reconciliation_count inteiros; reference_source,coverage,timezone strings; elapsed_reference_seconds int|null; reference_percent string|null; reference_denominators objeto; as_of,period_start,period_end_exclusive instantes. Daily: date_from,date_to,as_of e configuração; results com date,premium_seconds,gameplay_seconds,other_gameplay_seconds,reference_seconds (int|null),reference_source,reference_denominators e is_partial_day (bool). Summary substitui results por totais, reference_seconds nullable, reference_denominators, distribution [{activity_id,activity_name,seconds,session_count}], pending_reconciliation_count,coverage. Dados antigos imprecisos produzem coverage partial.

## RetroGames

| Método e caminho | Entrada | Saída/status |
|---|---|---|
| GET retro-generations/; GET retro-generations/{id}/ | nenhum | array/objeto RetroGeneration |
| GET retro-platforms/; GET retro-platforms/{id}/ | generation_id opcional | array/objeto RetroPlatform |
| GET retro-games/; GET retro-games/{id}/ | generation_id,platform_id,tier,search,status,active; paginação | página/objeto RetroGame |
| POST retro-games/{id}/start/ | PremiumStartSerializer | 201/200 Execution |
| PATCH retro-games/{id}/progress/ | RetroProgressRequestSerializer | 200 RetroProgress; 409 versão/estado |
| GET retro-games/{id}/sessions/ | paginação | página de execution_id,started_at?,completed_at,duration_seconds,coverage,execution_origin |

Catálogo continua legível com flag desabilitada. Início: 503 retrogames_disabled; 404 retro_game_not_found; 422 retro_game_inactive; 400 retro_hierarchy_invalid/invalid_duration/invalid_request/return_group_not_found; demais conflitos 409. A geração da plataforma deve coincidir com category da Activity e pertencer a grupo is_retro_catalog. Filtro active omitido exige jogo/plataforma/atividade ativos; active=true só filtra jogo ativo. Status: not_started,in_progress,completed,skipped. Progresso é por chave, versão inicial 0. played_seconds deriva de facts, open_estimate_seconds é separado, progress_percent pode superar 100; coverage indica precisão. Sessões podem vir de qualquer origem da mesma atividade.

## Metas semanais

| Método e caminho | Entrada | Saída/status |
|---|---|---|
| GET weekly-goals/; GET weekly-goals/{id}/ | active=true/false na lista; paginação | página/objeto WeeklyGoal |
| POST weekly-goals/ | WeeklyGoalCreateSerializer | 201 WeeklyGoal |
| PATCH weekly-goals/{id}/ | WeeklyGoalUpdateSerializer | 200 WeeklyGoal |
| GET weekly-goals/progress/ | week_start opcional; include=activity_signals uma vez; paginação | 200 progresso semanal |

Exatamente um destino group_id/category_id, metric=minutes/sessions, target positivo. Atualização exige expected_version e target ou active. Campos desconhecidos rejeitados. Semana começa segunda em São Paulo, consulta só até semana atual. Edições são versionadas para semana vigente; sem DELETE. 400 invalid_goal/invalid_week/invalid_include; 404 goal_not_found; 409 goal_already_exists/goal_version_conflict. Cada linha de progresso contém goal_id,metric,group_id?,category_id?,destination (type,id,name,color,group_id,group_name),target,achieved,remaining,progress_percent (string decimal),is_achieved e activity_signals {skip_count,distinct_activities_skipped,most_skipped opcional [{activity_id,name,skip_count}]}. Progresso inclui week_start,week_end_exclusive,timezone,as_of,pending_reconciliation_count e página de metas com estatísticas; sinais opcionais não alteram contagem nem bloqueiam início. Estrutura detalhada e exemplos dos sinais: [handoff de sinais](handoffs/METAS_SEMANAIS_SINAIS_FRONTEND.md), subordinado às regras centrais deste arquivo.

## Rotinas

| Método e caminho | Serializer/consulta | Resposta |
|---|---|---|
| GET routines/ | nenhum | plano: state,version,timezone,revisions |
| POST/PATCH routines/ | RevisionSerializer | plano (201 POST, 200 PATCH) |
| POST routines/template/ | TemplateSerializer | plano 200 |
| PUT routines/exception/ | ExceptionSerializer | `{version:int}` |
| PUT routines/suspension/ | SuspensionSerializer | `{version:int}` |
| GET routines/agenda/ | AgendaSerializer na query | agenda com days,occurrences,recorded_occurrences,as_of,version,timezone |
| GET routines/context/ | nenhum | contexto atual/próximo, as_of,timezone,version |
| GET/PUT routines/activity-preference/ | GET activity_id; PUT ActivityPreferenceSerializer | activity_id,version e campos de classificação nullable |
| GET/PUT routines/selection/ | GET IdentitySerializer; PUT SelectionSerializer | identidade,versão e fonte selecionada nullable |
| GET routines/preview/ | IdentitySerializer na query | prévia,disponibilidade,motivos,preview_token e contexto da fonte |
| POST routines/start/ | StartSerializer | Execution 201/200 |
| GET routines/summary/ | IntervalSerializer na query | resumo por interseção civil |
| GET routines/recommendations/ | page positivo, padrão 1 | context,count,page,page_size=100,results |

Datas 2000–2100; consulta de intervalo 1–31 dias inclusivos. Até 64 blocos; weekday 0=segunda; end_day_offset 0/1; sem sobreposição; fim posterior ao início no mesmo dia. Perfil especial só gameplay; min/max pareados apenas cardio. Cancel não aceita replacement, outras ações exigem. Seleção premium exige só premium_period_id; queue exige só group_id. Token de prévia assinado pertence à ocorrência/escopo/contexto: stale_preview exige nova prévia e nova decisão, nunca iniciar outra atividade silenciosamente. Duração da fila é fixa. Rotina é orientativa, não interrompe timer. Sessões são associadas pelo bloco real em starts_at, não pelo bloco escolhido.

Respostas aninhadas, exemplos completos e semântica de agenda/summary estão no [suplemento de rotinas](contracts/spec-017-routines.md) e fixtures [rotinas](contracts/spec-017-routines.json)/[sessões](contracts/spec-017-routine-sessions.json), incorporados por referência. Não somar sessions de occurrences e recorded_occurrences duas vezes. Snapshots permanecem após edição do plano. Confirmado só vem de fact preciso; estimativa não é cumprimento. Erros: 400 invalid_routine/invalid_interval e regras de data; 404 not_found; 409 stale_routine_version,stale_preference_version,overlap,occurrence_ended,stale_preview,source_unavailable e conflitos canônicos.

## Filmes, sorteio e tiers

| Método e caminho | Entrada | Resposta |
|---|---|---|
| GET movie-collections/ | CatalogQuery/paginação | página de Collection |
| GET movie-collections/{id}/state/ | nenhum | State |
| POST movie-collections/{id}/draws/ | DrawRequest | Draw 201/200 replay |
| GET movie-collections/{id}/draws/by-request/{request_id}/ | UUID no caminho | Draw recuperado |
| POST movie-collections/{id}/draws/{draw_id}/accept/ | AcceptRequest | `{movie:Movie,progress:Progress,states:State[],state:State}` |
| POST movie-collections/{id}/draws/{draw_id}/dismiss/ | DismissRequest | `{movie:Movie,progress:Progress,states:State[],state:State}` |
| GET/PATCH movie-collections/{id}/tiers/ | PATCH TierRequest | Board |
| GET movies/ | CatalogQuery | página de Movie |
| GET movies/{id}/ | ID | Movie + collections |
| PATCH movies/{id}/progress/ | ProgressRequest | `{movie:Movie,progress:Progress,states:State[]}` |
| GET movies/progress-history/ | CatalogQuery | página de id,movie_id,name,request_id,previous_status,next_status,occurred_at,draw_id? |

Movie: id,activity_id int; name string; description string|null; release_year int; award_year int|null; runtime_minutes int|null; poster_url/watch_url string (podem vazias); active bool; progress {status:unwatched|watching|watched,version:int,started_at:instant|null,watched_at:instant|null}. Collection: id,slug,name,category_id + State. State: collection_id,version int; counts {total_active,unwatched,watching,watched,eligible_count} int; condition=empty_collection|collection_completed|no_eligible_movies|ready; current_draw Draw|null; current_draw_valid bool. Draw: id/request_id UUID; status string; animation_duration_ms,eligible_count,selected_index int; candidates array de snapshots; selected_at instante; resolved_at instante|null; movie Movie. O servidor escolhe filme/índice; cliente apenas anima. Aceitar inicia progresso de filme, não pomodoro. Requisições são idempotentes por UUID e payload, com versões otimistas de estado/progresso.

Board: collection_id,version,unrated_count,watched_count int; limits/counts objetos S,A,B,C,D; items [{movie_id:int,name:string,release_year:int,tier:string|null,active:bool}]. Só assistidos podem receber tier. Limites S=10,A/B/C=20,D=30; PATCH aplica conjunto atomicamente (até 100 filmes distintos), tier=null remove classificação; desfazer watched limpa ratings e altera versões. HTTP 400 invalid_input; 404 not_found; conflitos de MovieError **409** (apêndice), incluindo tier_full com tier,limit,count. Não existe retrieve de movie-collections/{id}/ nem CRUD público de catálogo de filmes.

## Exemplos de integração

Headers comuns acima. Requisição `POST /api/activities/7/start/`:

```json
{"queue_item_id":31}
```

Resposta 201 (recorte de Execution; apêndice enumera todos os campos):

```json
{"execution_id":99,"id":99,"schedule_id":99,"activity_id":7,"queue_item_id":31,"queue_id":12,"execution_origin":"queue","premium_period_id":2,"retro_game_id":null,"state":"running","planned_duration_seconds":1800,"version":1,"starts_at":"2026-10-17T15:00:00Z","expected_end_at":"2026-10-17T15:30:00Z","remaining_seconds":1800,"status":"started"}
```

`GET /api/activities/active/` retorna Execution sem aliases extras do start; 204 não deve ser passado ao parser JSON. Conflito `POST /api/premium-periods/2/start/`, body `{"duration_minutes":30,"request_id":"550e8400-e29b-41d4-a716-446655440000"}`: 409 (recorte):

```json
{"code":"active_execution_conflict","detail":"Já existe uma atividade em execução.","active_execution":{"execution_id":99,"execution_origin":"queue","queue_item_id":31,"premium_period_id":2,"retro_game_id":null}}
```

O active_execution real contém Activity e todos os campos Execution. Exemplo direto bem-sucedido tem queue_item_id/queue_id=null e execution_origin=premium_direct,premium_period_id=2. Retro usa POST retro-games/5/start/ com o mesmo body e retorna retro_direct,retro_game_id=5,premium_period_id=null. Para concluir: `POST activities/complete/ {"schedule_id":99}` → 200 Execution completed. Para continuar: `POST activity-executions/99/continue/ {"duration_minutes":30,"request_id":"550e8400-e29b-41d4-a716-446655440001","expected_version":2}` → 201 sucessora (ou 200 replay).

Outros exemplos (objetos de resposta abreviados; não fixtures para parsing):

| Requisição | Resposta |
|---|---|
| POST activities/ `{"name":"Leitura","duration":25}` | 201 Activity com category default e campos calculados |
| GET activity-queue/activities/?group_id=1 | 200 `{"queue":null,"returned_count":0,"available_count":0,"has_more":false,"statistics_scope":"all_time","activities":[]}` sem fila |
| POST weekly-goals/ `{"metric":"minutes","group_id":1,"target":120}` | 201 `{"id":1,"metric":"minutes","group_id":1,"category_id":null,"is_all_groups":true,"target":120,"active":true,"effective_week":"2026-10-12","version":1}` para grupo default |
| PATCH gameplay-tracking-settings/ `{"expected_version":0,"daily_reference_minutes":300,"group_ids":[]}` | 200 `{"daily_reference_minutes":300,"reference_source":"fixed_daily","group_ids":[],"version":1,"timezone":"America/Sao_Paulo"}` |
| POST routines/ `{"expected_version":0,"effective_from":"2026-10-18","blocks":[]}` | 201 plano com version=1 e revisão sem blocos |
| POST movie-collections/1/draws/ `{"request_id":"550e8400-e29b-41d4-a716-446655440002","expected_state_version":0}` | 201 Draw; 409 se estado mudou/sem elegíveis |
| PATCH movie-collections/1/tiers/ `{"request_id":"550e8400-e29b-41d4-a716-446655440003","expected_version":0,"moves":[{"movie_id":2,"tier":"S"}]}` | 200 Board version=1, se filme assistido e elegível |

## Pendências, compatibilidade e migração

1. **Incompatibilidade existente no cliente:** permitir fila+premium conforme matriz, incluindo active_execution e snapshots locais. Publicar correção do parser antes de habilitar inícios diretos; não remover atribuição premium do backend.
2. Respostas de conflito de routines/start não incluem active_execution, ao contrário dos demais inícios. Uniformização futura deve atualizar esta referência e testes; recuperação atual via GET active/.
3. Há entradas de ID/versão legadas convertidas sem serializer (ex.: queue_item_id no start e expected_version no PATCH premium), `.get()` sem tradução de DoesNotExist no serviço premium/PATCH e FK return_group_id premium sem checagem equivalente à Retro. Valores malformados/IDs inexistentes podem resultar em **500** em vez do 400/404 desejável; não documentar validação inexistente. PATCH premium também permite activity no serializer, mas verifica sobreposição com a atividade anterior: revisar a mutabilidade desse campo antes de permitir sua troca no cliente.
4. Histórico activities é global, enquanto premium-analytics/history é filtrado por escopo. Não usar primeiro como histórico privado por chave. DELETE Activity pode encontrar relações PROTECT sem envelope público amigável.
5. Legacy e registros temporais incompletos exigem tolerância a null. Campos de método podem omitir contexto se relações antigas faltarem. Versão/IDs do estado corrente devem vir do servidor; após conflito, reler antes de repetir escrita com novo UUID.
6. Esta entrega é documental: nenhuma mudança incompatível no servidor, migração de banco ou backfill. Mudanças futuras de tipo, nullabilidade, validação, status, erros ou significado exigem histórico, orientação de migração e atualização conjunta do cliente. Mudança aditiva não autoriza o cliente a ignorar invariantes; aceitar campos novos sem rejeitar payload inteiro.

## Fontes e histórico contratual

Este arquivo prevalece como referência integrada. Specs em `docs/specs/` e handoffs descrevem decisões e entregas históricas, não contratos paralelos vigentes. JSONs em `docs/contracts/` e coleção [Postman](postman/backend_pomodoro_task.postman_collection.json) são exemplos/fixtures auxiliares, podem ser parciais e não substituem tabelas de campos nem matriz acima. O suplemento de rotinas mantém detalhes extensos incorporados por referência; divergências devem ser corrigidas aqui e no suplemento na mesma entrega.

| Marco | Alteração | Compatibilidade/migração |
|---|---|---|
| SPEC-BACK-006 a 011 | timer canônico, filas persistentes por grupo, contexto, recriação/listagem | clientes devem usar queue_item_id e instantes do servidor; IDs de Activity não identificam execução |
| SPEC-BACK-012/013 | metas versionadas e sinais opcionais | adição de endpoints; include opt-in |
| SPEC-014 | períodos, início direto, relatórios e continuação | clientes precisam aceitar queue_item_id null nas diretas; fila pode ter premium_period_id; importar legado conforme README |
| SPEC-015 | catálogo/progresso e retro_direct | aceitar retro_game_id e origem; flag controla novos inícios |
| SPEC-017 / extensão 04/10/2026 | rotinas, prévia, associações/snapshots | campos aditivos nullable; sem backfill automático; deduplicar execution_id na agenda |
| SPEC-018 e tiers | filmes, sorteio idempotente e classificação | módulo separado do timer; versões/UUIDs nas escritas |
| 17/10/2026 | centralização deste contrato e regra permanente no AGENTS | sem alteração de runtime; registrada incompatibilidade Flutter queue+premium e migração proposta |

## Validação e rastreabilidade

Fontes: `config/urls.py`, `apps/pomodoro/urls.py`, `views.py`, `routine_views.py`, `weekly_goal_views.py`, `movie_views.py`, serializers correspondentes, models e services. Testes: `tests/`, `apps/pomodoro/tests.py`, `test_spec_back_008/009/010/011.py`, `test_spec_014.py`, `test_spec_015.py`, `test_spec_017.py`, `test_routine_sessions.py`, `test_weekly_goals.py`, `test_spec_018.py`, `test_movie_tiers.py`. Perfis `*_postgres.py` exigem PostgreSQL descartável e não substituem checagens SQLite. Não houve acesso a banco real nem alteração do frontend.

Comando seguro: `APP_ENV=test TEST_DATABASE_URL=sqlite:///tests/.tmp/contracts.sqlite DJANGO_SETTINGS_MODULE=config.settings.test .venv/bin/python manage.py test --noinput`. Inventário abaixo conferido por introspecção do router e serializers com settings de teste; erros extraídos das chamadas de exceção e revisados conforme status nas views. A presença de um código no serviço não garante sua exposição por todas as rotas.

Resultado final: **308 testes, OK, 20 ignorados** (perfis opcionais, incluindo concorrência PostgreSQL). O novo teste de regressão confirma queue+premium no início, recuperação e active_execution; os **7 testes de SPEC-014 também passaram** na execução específica. Doctor sem erros (aviso de PERSONAL_SKILLS_HOME ausente; configuração local reconhecida), links locais e git diff --check válidos.

## Tipos JSON dos campos derivados

Os tipos DRF do apêndice correspondem a JSON: IntegerField/PrimaryKeyRelatedField → inteiro; BooleanField → boolean; CharField/ChoiceField/UUIDField → string; DateField/TimeField/DateTimeField → string formatada; ListField/ListSerializer → array; JSONField/SerializerMethodField → estrutura descrita abaixo. `allow_null` de campo somente leitura não é garantia de presença em dados legados: o serializer pode emitir null vindo da relação/modelo.

| Estrutura/campo derivado | Tipo e nullabilidade de saída |
|---|---|
| Activity.can_execute, is_premium_active | bool |
| Activity.remaining_executions | int ou null sem categoria (0 quando cota esgotada) |
| Activity.group_id/group_name | int/string; relações ausentes podem omitir campos no legado |
| QueueItem.activity / Execution.activity | objeto Activity obrigatório nas sessões válidas |
| QueueItem.id / queue_item_id | int; primeiro é Activity.id; segundo é item.id |
| queue_id,queue_group_id | int ou null sem fila |
| queue_group_name,queue_mode | string ou null sem fila |
| skip_locked | bool ou null sem fila |
| group_max_daily_minutes,group_consumed_daily_minutes,group_remaining_daily_minutes | int ou null sem fila; max=0 representa sem limite e remaining=null mesmo com fila |
| QueueItem.source_queue_id | int ou null (origem da revisão) |
| QueueItem.description | string ou null se descrição da Activity for null |
| History.completed | bool (end_time != null) |
| PremiumPeriod.effective_end_at | instante ISO 8601; status=future/active/ended |
| RetroGame.generation | {id:int,name:string,sort_order:int} |
| RetroGame.platform | {id:int,name:string,slug:string} |
| RetroGame.status,coverage | string |
| RetroGame.progress_version,played_seconds,open_estimate_seconds | int |
| RetroGame.progress_percent | número JSON, duas casas no cálculo |
| RetroGame.can_start | bool; disponibilidade de catálogo/flag, não promessa de ausência de conflito global |
| Execution.routine_occurrence,routine_requested_occurrence | objeto ou null; definidos na seção de execução |
| Execution.remaining_seconds / server_now | int / instante ISO 8601 |

## Apêndice: campos dos serializers

S = saída somente (não obrigatório como entrada); E/S = entrada e saída no serializer; E = entrada de ações. A disponibilidade HTTP prevalece: serializers de catálogos read-only não criam endpoints de escrita; serializers de resposta de fila/metas são usados apenas na saída pelas respectivas views. “Obrigatório” refere-se a criação/ação, não a PATCH parcial. null refere-se a aceitação declarada de entrada; campos derivados de saída têm nullabilidade e estrutura descritas nas seções anteriores. Relações são IDs inteiros; serializers aninhados têm sua própria tabela. Defaults de modelo podem ser aplicados mesmo sem default explícito no serializer.

### GroupSerializer

Fonte: `apps/pomodoro/serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| id | IntegerField | S | não | não |  |
| name | CharField | E/S | sim | não | max_length=50; allow_blank=False |
| description | CharField | E/S | não | sim | allow_blank=True |
| color | CharField | E/S | não | não | max_length=7; allow_blank=False |
| is_default | BooleanField | E/S | não | não |  |
| is_retro_catalog | BooleanField | E/S | não | não |  |
| max_daily_minutes | IntegerField | E/S | não | não | min_value=0; max_value=9223372036854775807 |

### CategorySerializer

Fonte: `apps/pomodoro/serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| id | IntegerField | S | não | não |  |
| name | CharField | E/S | sim | não | max_length=50; allow_blank=False |
| color | CharField | E/S | não | não | max_length=7; allow_blank=False |
| group | PrimaryKeyRelatedField | E/S | não | não |  |
| group_name | CharField | S | não | não | allow_blank=False |

### ActivitySerializer

Fonte: `apps/pomodoro/serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| id | IntegerField | S | não | não |  |
| name | CharField | E/S | sim | não | max_length=100; allow_blank=False |
| description | CharField | E/S | não | sim | allow_blank=True |
| duration | IntegerField | E/S | não | não | min_value=-9223372036854775808; max_value=9223372036854775807 |
| active | BooleanField | E/S | não | não |  |
| premium | BooleanField | E/S | não | não |  |
| premium_from | DateField | E/S | não | sim |  |
| premium_until | DateField | E/S | não | sim |  |
| is_premium_active | BooleanField | S | não | não |  |
| category | PrimaryKeyRelatedField | E/S | não | não |  |
| created_at | DateTimeField | S | não | não |  |
| last_executed | DateTimeField | E/S | não | sim |  |
| executions_today | IntegerField | E/S | não | não | min_value=-9223372036854775808; max_value=9223372036854775807 |
| can_execute | SerializerMethodField | S | não | não |  |
| remaining_executions | SerializerMethodField | S | não | não |  |
| group_id | IntegerField | S | não | não |  |
| group_name | CharField | S | não | não | allow_blank=False |

### HistorySerializer

Fonte: `apps/pomodoro/serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| id | IntegerField | S | não | não |  |
| activity_name | CharField | S | não | não | allow_blank=False |
| category_name | CharField | S | não | não | allow_blank=False |
| group_name | CharField | S | não | não | allow_blank=False |
| start_time | DateTimeField | E/S | sim | não |  |
| end_time | DateTimeField | E/S | não | sim |  |
| duration | IntegerField | E/S | não | sim | min_value=-9223372036854775808; max_value=9223372036854775807 |
| completed | SerializerMethodField | S | não | não |  |

### ActivityQueueItemSerializer

Fonte: `apps/pomodoro/serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| queue_item_id | IntegerField | S | não | não |  |
| queue_id | SerializerMethodField | S | não | não |  |
| id | IntegerField | S | não | não |  |
| name | CharField | S | não | não | allow_blank=False |
| description | CharField | S | não | não | allow_blank=False |
| duration | IntegerField | S | não | não |  |
| category | IntegerField | S | não | não |  |
| group_id | IntegerField | S | não | não |  |
| group_name | CharField | S | não | não | allow_blank=False |
| premium | BooleanField | S | não | não |  |
| is_premium_active | BooleanField | S | não | não |  |
| activity | ActivitySerializer | S | não | não |  |
| queue_mode | SerializerMethodField | S | não | não |  |
| pool_number | IntegerField | S | não | não |  |
| pool_size | IntegerField | S | não | não |  |
| consumed_count | IntegerField | S | não | não |  |
| skip_locked | SerializerMethodField | S | não | não |  |
| queue_group_id | SerializerMethodField | S | não | não |  |
| queue_group_name | SerializerMethodField | S | não | não |  |
| position | IntegerField | S | não | não |  |
| source_queue_id | IntegerField | S | não | não |  |
| group_max_daily_minutes | SerializerMethodField | S | não | não |  |
| group_consumed_daily_minutes | SerializerMethodField | S | não | não |  |
| group_remaining_daily_minutes | SerializerMethodField | S | não | não |  |
| state | ChoiceField | E/S | não | não | allow_blank=False; enum=pending,presented,started,completed,skipped,expired |

### ActivityExecutionSerializer

Fonte: `apps/pomodoro/serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| routine_occurrence | SerializerMethodField | S | não | não |  |
| routine_requested_occurrence | JSONField | S | não | não |  |
| execution_id | IntegerField | S | não | não |  |
| id | IntegerField | S | não | não |  |
| queue_id | SerializerMethodField | S | não | não |  |
| queue_item_id | IntegerField | S | não | sim |  |
| queue_group_id | SerializerMethodField | S | não | não |  |
| queue_group_name | SerializerMethodField | S | não | não |  |
| queue_mode | SerializerMethodField | S | não | não |  |
| skip_locked | SerializerMethodField | S | não | não |  |
| group_max_daily_minutes | SerializerMethodField | S | não | não |  |
| group_consumed_daily_minutes | SerializerMethodField | S | não | não |  |
| group_remaining_daily_minutes | SerializerMethodField | S | não | não |  |
| state | ChoiceField | E/S | não | não | allow_blank=False; enum=preparing,running,completed,cancelled,expired |
| activity | ActivitySerializer | S | não | não |  |
| requested_at | DateTimeField | S | não | não |  |
| starts_at | DateTimeField | S | não | não |  |
| expected_end_at | DateTimeField | S | não | não |  |
| completed_at | DateTimeField | S | não | não |  |
| remaining_seconds | SerializerMethodField | S | não | não |  |
| server_now | SerializerMethodField | S | não | não |  |
| version | IntegerField | E/S | não | não | min_value=0; max_value=9223372036854775807 |
| execution_origin | ChoiceField | E/S | não | não | allow_blank=False; enum=queue,premium_direct,retro_direct,legacy |
| planned_duration_seconds | IntegerField | E/S | não | sim | min_value=0; max_value=9223372036854775807 |
| premium_period_id | ReadOnlyField | S | não | não |  |
| retro_game_id | ReadOnlyField | S | não | não |  |
| continued_from_id | ReadOnlyField | S | não | não |  |
| return_group_id | ReadOnlyField | S | não | não |  |

### PremiumPeriodSerializer

Fonte: `apps/pomodoro/serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| id | IntegerField | S | não | não |  |
| activity | PrimaryKeyRelatedField | E/S | sim | não |  |
| activity_name | CharField | S | não | não | allow_blank=False |
| activity_group_id | IntegerField | S | não | não |  |
| activity_group_name | CharField | S | não | não | allow_blank=False |
| kind | ChoiceField | E/S | sim | não | allow_blank=False; enum=paid,focus |
| title | CharField | E/S | sim | não | max_length=120; allow_blank=False |
| starts_on | DateField | E/S | sim | não |  |
| ends_on | DateField | E/S | sim | não |  |
| timezone | CharField | E/S | não | não | max_length=64; allow_blank=False |
| ended_early_at | DateTimeField | S | não | sim |  |
| effective_end_at | SerializerMethodField | S | não | não |  |
| status | SerializerMethodField | S | não | não |  |
| source | CharField | S | não | não | allow_blank=False |
| version | IntegerField | S | não | não |  |
| created_at | DateTimeField | S | não | não |  |
| updated_at | DateTimeField | S | não | não |  |

### PremiumStartSerializer

Fonte: `apps/pomodoro/serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| duration_minutes | IntegerField | E/S | sim | não | min_value=1; max_value=720 |
| request_id | UUIDField | E/S | sim | não |  |
| return_group_id | IntegerField | E/S | não | sim | min_value=1 |

### PremiumContinueSerializer

Fonte: `apps/pomodoro/serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| duration_minutes | IntegerField | E/S | sim | não | min_value=1; max_value=720 |
| request_id | UUIDField | E/S | sim | não |  |
| return_group_id | IntegerField | E/S | não | sim | min_value=1 |
| expected_version | IntegerField | E/S | sim | não | min_value=1 |

### RetroGenerationSerializer

Fonte: `apps/pomodoro/serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| id | IntegerField | S | não | não |  |
| name | CharField | E/S | sim | não | max_length=50; allow_blank=False |
| description | CharField | E/S | não | sim | allow_blank=True |
| color | CharField | E/S | não | não | max_length=7; allow_blank=False |
| sort_order | IntegerField | E/S | sim | não |  |

### RetroPlatformSerializer

Fonte: `apps/pomodoro/serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| id | IntegerField | S | não | não |  |
| generation_id | IntegerField | S | não | não |  |
| name | CharField | E/S | sim | não | max_length=100; allow_blank=False |
| slug | SlugField | E/S | sim | não | max_length=100; allow_blank=False |
| manufacturer | CharField | E/S | não | não | max_length=100; allow_blank=True |
| release_year | IntegerField | E/S | não | sim | min_value=0; max_value=9223372036854775807 |
| sort_order | IntegerField | E/S | sim | não | min_value=0; max_value=9223372036854775807 |
| active | BooleanField | E/S | não | não |  |

### RetroGameSerializer

Fonte: `apps/pomodoro/serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| id | IntegerField | S | não | não |  |
| activity_id | IntegerField | S | não | não |  |
| name | CharField | S | não | não | allow_blank=False |
| description | CharField | S | não | sim | allow_blank=False |
| default_block_minutes | IntegerField | S | não | não |  |
| generation | SerializerMethodField | S | não | não |  |
| platform | SerializerMethodField | S | não | não |  |
| tier | ChoiceField | E/S | sim | não | allow_blank=False; enum=essential,complementary |
| estimated_main_minutes | IntegerField | E/S | sim | não | min_value=0; max_value=9223372036854775807 |
| play_goal | CharField | E/S | não | não | max_length=240; allow_blank=True |
| release_year | IntegerField | E/S | não | sim | min_value=0; max_value=9223372036854775807 |
| sort_order | IntegerField | E/S | sim | não | min_value=0; max_value=9223372036854775807 |
| active | BooleanField | E/S | não | não |  |
| cover_url | URLField | E/S | não | sim | max_length=200; allow_blank=True |
| status | SerializerMethodField | S | não | não |  |
| progress_version | SerializerMethodField | S | não | não |  |
| played_seconds | SerializerMethodField | S | não | não |  |
| open_estimate_seconds | SerializerMethodField | S | não | não |  |
| progress_percent | SerializerMethodField | S | não | não |  |
| coverage | SerializerMethodField | S | não | não |  |
| can_start | SerializerMethodField | S | não | não |  |

### RetroProgressRequestSerializer

Fonte: `apps/pomodoro/serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| status | ChoiceField | E/S | sim | não | allow_blank=False; enum=in_progress,completed,skipped |
| expected_version | IntegerField | E/S | sim | não | min_value=0 |

### RetroProgressSerializer

Fonte: `apps/pomodoro/serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| status | ChoiceField | E/S | sim | não | allow_blank=False; enum=in_progress,completed,skipped |
| started_at | DateTimeField | E/S | não | sim |  |
| completed_at | DateTimeField | E/S | não | sim |  |
| version | IntegerField | E/S | não | não | min_value=0; max_value=9223372036854775807 |

### QueueRecreationRequestSerializer

Fonte: `apps/pomodoro/serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| group_id | IntegerField | E/S | não | não | min_value=1 |
| expected_queue_id | IntegerField | E/S | sim | não | min_value=1 |

### QueueRecreationResponseSerializer

Fonte: `apps/pomodoro/serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| queue_id | IntegerField | E/S | sim | não |  |
| recreated_from_queue_id | IntegerField | E/S | sim | não |  |
| queue_group_id | IntegerField | E/S | sim | não |  |
| queue_group_name | CharField | E/S | sim | não | allow_blank=False |
| queue_mode | CharField | E/S | sim | não | allow_blank=False |
| pool_number | IntegerField | E/S | sim | não |  |
| pool_size | IntegerField | E/S | sim | não |  |
| skip_locked | BooleanField | E/S | sim | não |  |
| requeued_skipped_count | IntegerField | E/S | sim | não |  |
| skipped_not_requeued | ListField | E/S | sim | não | allow_empty=True |

### QueueActivityCategorySerializer

Fonte: `apps/pomodoro/serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| id | IntegerField | E/S | sim | não |  |
| name | CharField | E/S | sim | não | allow_blank=False |

### QueueActivitySummarySerializer

Fonte: `apps/pomodoro/serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| queue_item_id | IntegerField | E/S | sim | não |  |
| position | IntegerField | E/S | sim | não |  |
| state | CharField | E/S | sim | não | allow_blank=False |
| activity_id | IntegerField | E/S | sim | não |  |
| name | CharField | E/S | sim | não | allow_blank=False |
| category | QueueActivityCategorySerializer | E/S | sim | não |  |
| execution_count | IntegerField | E/S | sim | não |  |
| last_execution_at | DateTimeField | E/S | sim | sim |  |
| skip_count | IntegerField | E/S | sim | não |  |

### ActiveQueueSummarySerializer

Fonte: `apps/pomodoro/serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| id | IntegerField | E/S | sim | não |  |
| group_id | IntegerField | E/S | sim | não |  |
| group_name | CharField | E/S | sim | não | allow_blank=False |
| mode | CharField | E/S | sim | não | allow_blank=False |
| pool_number | IntegerField | E/S | sim | não |  |
| pool_size | IntegerField | E/S | sim | não |  |
| skip_locked | BooleanField | E/S | sim | não |  |

### WeeklyGoalCreateSerializer

Fonte: `apps/pomodoro/serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| metric | ChoiceField | E/S | sim | não | allow_blank=False; enum=minutes,sessions |
| group_id | PrimaryKeyRelatedField | E/S | não | não |  |
| category_id | PrimaryKeyRelatedField | E/S | não | não |  |
| target | IntegerField | E/S | sim | não | min_value=1; max_value=2147483647 |

### WeeklyGoalUpdateSerializer

Fonte: `apps/pomodoro/serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| target | IntegerField | E/S | não | não | min_value=1; max_value=2147483647 |
| active | BooleanField | E/S | não | não |  |
| expected_version | IntegerField | E/S | sim | não | min_value=1; max_value=2147483647 |

### WeeklyGoalSerializer

Fonte: `apps/pomodoro/serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| id | IntegerField | E/S | sim | não |  |
| metric | CharField | E/S | sim | não | allow_blank=False |
| group_id | IntegerField | E/S | sim | sim |  |
| category_id | IntegerField | E/S | sim | sim |  |
| is_all_groups | BooleanField | E/S | sim | não |  |
| target | IntegerField | E/S | sim | não |  |
| active | BooleanField | E/S | sim | não |  |
| effective_week | DateField | E/S | sim | não |  |
| version | IntegerField | E/S | sim | não |  |

### QueueActivityListResponseSerializer

Fonte: `apps/pomodoro/serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| queue | ActiveQueueSummarySerializer | E/S | sim | sim |  |
| returned_count | IntegerField | E/S | sim | não |  |
| available_count | IntegerField | E/S | sim | não |  |
| has_more | BooleanField | E/S | sim | não |  |
| statistics_scope | CharField | E/S | sim | não | allow_blank=False |
| activities | ListSerializer de QueueActivitySummarySerializer | E/S | sim | não | allow_empty=True |

### BlockSerializer

Fonte: `apps/pomodoro/routine_serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| block_id | UUIDField | E | sim | não |  |
| weekday | IntegerField | E | sim | não | min_value=0; max_value=6 |
| kind | ChoiceField | E | sim | não | allow_blank=False; enum=gameplay,cardio,family |
| profile | ChoiceField | E | sim | não | allow_blank=False; enum=general,focus,interruptible |
| start_time | TimeField | E | sim | não |  |
| end_time | TimeField | E | sim | não |  |
| end_day_offset | IntegerField | E | não | não | min_value=0; max_value=1; default=0 |
| expected_min_minutes | IntegerField | E | não | sim | min_value=1; max_value=1440; default=None |
| expected_max_minutes | IntegerField | E | não | sim | min_value=1; max_value=1440; default=None |

### ReplacementSerializer

Fonte: `apps/pomodoro/routine_serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| kind | ChoiceField | E | sim | não | allow_blank=False; enum=gameplay,cardio,family |
| profile | ChoiceField | E | sim | não | allow_blank=False; enum=general,focus,interruptible |
| start_time | TimeField | E | sim | não |  |
| end_time | TimeField | E | sim | não |  |
| end_day_offset | IntegerField | E | não | não | min_value=0; max_value=1; default=0 |
| expected_min_minutes | IntegerField | E | não | sim | min_value=1; max_value=1440; default=None |
| expected_max_minutes | IntegerField | E | não | sim | min_value=1; max_value=1440; default=None |
| starts_on | RoutineDateField | E | não | não |  |

### VersionSerializer

Fonte: `apps/pomodoro/routine_serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| expected_version | IntegerField | E | sim | não | min_value=0 |

### RevisionSerializer

Fonte: `apps/pomodoro/routine_serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| expected_version | IntegerField | E | sim | não | min_value=0 |
| effective_from | RoutineDateField | E | sim | não |  |
| blocks | ListSerializer de BlockSerializer | E | sim | não | max_length=64; allow_empty=True |

### TemplateSerializer

Fonte: `apps/pomodoro/routine_serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| expected_version | IntegerField | E | sim | não | min_value=0 |
| effective_from | RoutineDateField | E | sim | não |  |

### ExceptionSerializer

Fonte: `apps/pomodoro/routine_serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| expected_version | IntegerField | E | sim | não | min_value=0 |
| origin_date | RoutineDateField | E | sim | não |  |
| block_id | UUIDField | E | sim | não |  |
| action | ChoiceField | E | sim | não | allow_blank=False; enum=cancel,move,shorten,replace |
| replacement | ReplacementSerializer | E | não | não |  |

### SuspensionSerializer

Fonte: `apps/pomodoro/routine_serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| expected_version | IntegerField | E | sim | não | min_value=0 |
| date | RoutineDateField | E | sim | não |  |
| suspended | BooleanField | E | sim | não |  |

### IdentitySerializer

Fonte: `apps/pomodoro/routine_serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| origin_date | RoutineDateField | E | sim | não |  |
| block_id | UUIDField | E | sim | não |  |

### SelectionSerializer

Fonte: `apps/pomodoro/routine_serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| origin_date | RoutineDateField | E | sim | não |  |
| block_id | UUIDField | E | sim | não |  |
| expected_version | IntegerField | E | sim | não | min_value=0 |
| source | ChoiceField | E | sim | não | allow_blank=False; enum=premium,queue |
| premium_period_id | IntegerField | E | não | não | min_value=1 |
| group_id | IntegerField | E | não | não | min_value=1 |
| return_group_id | IntegerField | E | não | sim | min_value=1 |

### ActivityPreferenceSerializer

Fonte: `apps/pomodoro/routine_serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| expected_version | IntegerField | E | sim | não | min_value=0 |
| activity_id | IntegerField | E | sim | não | min_value=1 |
| pause_immediately | BooleanField | E | não | sim |  |
| online | BooleanField | E | não | sim |  |
| requires_group | BooleanField | E | não | sim |  |
| competitive | BooleanField | E | não | sim |  |
| focus_suitable | BooleanField | E | não | sim |  |
| minimum_minutes | IntegerField | E | não | sim | min_value=1; max_value=720 |

### StartSerializer

Fonte: `apps/pomodoro/routine_serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| origin_date | RoutineDateField | E | sim | não |  |
| block_id | UUIDField | E | sim | não |  |
| preview_token | CharField | E | sim | não | max_length=8192; allow_blank=False |
| request_id | UUIDField | E | sim | não |  |
| duration_minutes | IntegerField | E | não | não | min_value=1; max_value=720 |

### IntervalSerializer

Fonte: `apps/pomodoro/routine_serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| date_from | RoutineDateField | E | sim | não |  |
| date_to | RoutineDateField | E | sim | não |  |

### TrackingSettingsSerializer

Fonte: `apps/pomodoro/routine_serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| expected_version | IntegerField | E | sim | não | min_value=0 |
| daily_reference_minutes | IntegerField | E | não | não | min_value=1; max_value=1440 |
| group_ids | ListField | E | não | não | max_length=1000; allow_empty=True |
| reference_source | ChoiceField | E | não | não | allow_blank=False; enum=fixed_daily,routine |

### AgendaSerializer

Fonte: `apps/pomodoro/routine_serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| date_from | RoutineDateField | E | sim | não |  |
| date_to | RoutineDateField | E | sim | não |  |
| sessions_page | IntegerField | E | não | não | min_value=1; max_value=1000000; default=1 |

### DrawRequest

Fonte: `apps/pomodoro/movie_serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| request_id | UUIDField | E | sim | não |  |
| expected_state_version | IntegerField | E | sim | não | min_value=0 |

### DismissRequest

Fonte: `apps/pomodoro/movie_serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| request_id | UUIDField | E | sim | não |  |
| expected_state_version | IntegerField | E | sim | não | min_value=0 |

### AcceptRequest

Fonte: `apps/pomodoro/movie_serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| request_id | UUIDField | E | sim | não |  |
| expected_state_version | IntegerField | E | sim | não | min_value=0 |
| expected_progress_version | IntegerField | E | sim | não | min_value=0 |

### ProgressRequest

Fonte: `apps/pomodoro/movie_serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| request_id | UUIDField | E | sim | não |  |
| expected_version | IntegerField | E | sim | não | min_value=0 |
| status | ChoiceField | E | sim | não | allow_blank=False; enum=unwatched,watching,watched |
| draw_id | UUIDField | E | não | não |  |

### CatalogQuery

Fonte: `apps/pomodoro/movie_serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| collection_id | IntegerField | E | não | não | min_value=1 |
| status | ChoiceField | E | não | não | allow_blank=False; enum=unwatched,watching,watched |
| search | CharField | E | não | não | max_length=200; allow_blank=True |
| page | IntegerField | E | não | não | min_value=1 |
| page_size | IntegerField | E | não | não | min_value=1; max_value=100 |

### TierMove

Fonte: `apps/pomodoro/movie_serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| movie_id | IntegerField | E | sim | não | min_value=1 |
| tier | ChoiceField | E | sim | sim | allow_blank=False; enum=S,A,B,C,D |

### TierRequest

Fonte: `apps/pomodoro/movie_serializers.py`.

| Campo | Tipo DRF | Entrada | Obrigatório | null | Restrições/default |
|---|---|---|---|---|---|
| request_id | UUIDField | E | sim | não |  |
| expected_version | IntegerField | E | sim | não | min_value=0 |
| moves | ListSerializer de TierMove | E | sim | não | max_length=100; allow_empty=False |

## Apêndice: inventário resolvido de rotas

Inclui aliases explícitos e rotas do router. Métodos abaixo já respeitam http_method_names da view; aliases explícitos anteriores têm precedência. Formatos opcionais `.json` do router não são enumerados; usar a forma com barra final. As tabelas principais definem corpos, status e regras.

| Caminho relativo a /api/ | Método → ação |
|---|---|
| `routines/` | GET → list, POST → create, PATCH → update_plan |
| `activities/history/` | GET → history |
| `activities/active/` | GET → active |
| `gameplay-tracking-settings/` | GET → list, PATCH → partial_update |
| `^movie-collections/$` | GET → list |
| `^movie-collections/(?P<pk>[^/.]+)/draws/(?P<draw_id>[0-9a-fA-F-]{36})/accept/$` | POST → accept |
| `^movie-collections/(?P<pk>[^/.]+)/draws/(?P<draw_id>[0-9a-fA-F-]{36})/dismiss/$` | POST → dismiss |
| `^movie-collections/(?P<pk>[^/.]+)/draws/$` | POST → draws |
| `^movie-collections/(?P<pk>[^/.]+)/draws/by-request/(?P<request_id>[0-9a-fA-F-]{36})/$` | GET → recover |
| `^movie-collections/(?P<pk>[^/.]+)/state/$` | GET → state |
| `^movie-collections/(?P<pk>[^/.]+)/tiers/$` | GET → tiers, PATCH → tiers |
| `^movies/$` | GET → list |
| `^movies/progress-history/$` | GET → progress_history |
| `^movies/(?P<pk>[^/.]+)/$` | GET → retrieve |
| `^movies/(?P<pk>[^/.]+)/progress/$` | PATCH → progress |
| `^routines/$` | GET → list, POST → create |
| `^routines/activity-preference/$` | GET → activity_preference, PUT → activity_preference |
| `^routines/agenda/$` | GET → agenda |
| `^routines/context/$` | GET → context |
| `^routines/exception/$` | PUT → exception |
| `^routines/preview/$` | GET → preview |
| `^routines/recommendations/$` | GET → recommendations |
| `^routines/selection/$` | GET → selection, PUT → selection |
| `^routines/start/$` | POST → start |
| `^routines/summary/$` | GET → summary |
| `^routines/suspension/$` | PUT → suspension |
| `^routines/template/$` | POST → template |
| `^weekly-goals/$` | GET → list, POST → create |
| `^weekly-goals/progress/$` | GET → progress |
| `^weekly-goals/(?P<pk>[0-9]+)/$` | GET → retrieve, PATCH → partial_update |
| `^groups/$` | GET → list |
| `^groups/(?P<pk>[^/.]+)/$` | GET → retrieve |
| `^categories/$` | GET → list |
| `^categories/(?P<pk>[^/.]+)/$` | GET → retrieve |
| `^activities/$` | GET → list, POST → create |
| `^activities/active/$` | GET → active |
| `^activities/complete/$` | POST → complete |
| `^activities/history/$` | GET → history |
| `^activities/next/$` | GET → next |
| `^activities/status/(?P<schedule_id>[^/.]+)/$` | GET → status |
| `^activities/(?P<pk>[^/.]+)/$` | GET → retrieve, PUT → update, PATCH → partial_update, DELETE → destroy |
| `^activities/(?P<pk>[^/.]+)/start/$` | POST → start |
| `^activity-queue/items/(?P<pk>[^/.]+)/skip/$` | POST → skip |
| `^activity-queue/activities/$` | GET → activities |
| `^activity-queue/recreate/$` | POST → recreate |
| `^activity-executions/(?P<pk>[^/.]+)/$` | GET → retrieve |
| `^activity-executions/(?P<pk>[^/.]+)/continue/$` | POST → continue_ |
| `^activity-executions/(?P<pk>[^/.]+)/reconcile/$` | POST → reconcile |
| `^premium-periods/$` | GET → list, POST → create |
| `^premium-periods/(?P<pk>[^/.]+)/$` | GET → retrieve, PATCH → partial_update |
| `^premium-periods/(?P<pk>[^/.]+)/end/$` | POST → end |
| `^premium-periods/(?P<pk>[^/.]+)/start/$` | POST → start |
| `^premium-periods/(?P<pk>[^/.]+)/stats/$` | GET → stats |
| `^premium-analytics/daily/$` | GET → daily |
| `^premium-analytics/history/$` | GET → history |
| `^premium-analytics/summary/$` | GET → summary |
| `^retro-generations/$` | GET → list |
| `^retro-generations/(?P<pk>[^/.]+)/$` | GET → retrieve |
| `^retro-platforms/$` | GET → list |
| `^retro-platforms/(?P<pk>[^/.]+)/$` | GET → retrieve |
| `^retro-games/$` | GET → list |
| `^retro-games/(?P<pk>[^/.]+)/$` | GET → retrieve |
| `^retro-games/(?P<pk>[^/.]+)/progress/$` | PATCH → progress |
| `^retro-games/(?P<pk>[^/.]+)/sessions/$` | GET → sessions |
| `^retro-games/(?P<pk>[^/.]+)/start/$` | POST → start |

## Apêndice: códigos de domínio por implementação

Status padrão: ActivityExecutionConflict/QueueConflict/MovieError 409; GoalError/RoutineError 400 salvo status explícito. As views podem remapear (especialmente Retro); ver tabelas acima. Esta relação também permite localizar regras e payloads de erro sem presumir um envelope uniforme.

| Serviço | Códigos literais |
|---|---|
| `activity_execution.py` | `active_execution_conflict`, `activity_already_scheduled_today`, `activity_no_longer_eligible`, `activity_start_conflict`, `daily_limit_reached`, `execution_not_completable`, `group_daily_minutes_reached`, `queue_item_consumed`, `queue_item_expired`, `queue_item_mismatch`, `queue_item_not_found`, `queue_item_unavailable`, `queue_reconciled` |
| `activity_queue.py` | `active_execution_running`, `active_queue_not_found`, `no_activity_available`, `queue_changed`, `queue_item_consumed`, `queue_item_expired`, `queue_item_not_found`, `queue_recreation_locked`, `skip_locked` |
| `gameplay_settings.py` | `group_not_found`, `stale_settings_version` |
| `movie_draw.py` | `collection_completed`, `empty_collection`, `no_eligible_movies`, `idempotency_conflict`, `movie_unavailable`, `pending_draw_exists`, `stale_progress_version`, `stale_state_version` |
| `movie_tiers.py` | `movie_not_watched`, `movie_outside_collection`, `stale_tier_version`, `tier_full` |
| `premium_execution.py` | `active_execution_conflict`, `continuation_context_conflict`, `continuation_payload_conflict`, `execution_not_found`, `idempotency_payload_conflict`, `idempotency_tombstone`, `invalid_duration`, `premium_direct_disabled`, `premium_period_not_active`, `premium_start_conflict`, `stale_execution_version` |
| `premium_periods.py` | `premium_period_overlap` |
| `retro_execution.py` | `active_execution_conflict`, `continuation_context_conflict`, `continuation_payload_conflict`, `execution_not_found`, `idempotency_payload_conflict`, `idempotency_tombstone`, `invalid_duration`, `retro_game_inactive`, `retro_game_not_found`, `retro_hierarchy_invalid`, `retro_start_conflict`, `retrogames_disabled`, `return_group_not_found`, `stale_execution_version` |
| `retro_progress.py` | `stale_progress_version` |
| `routine_selection.py` | `idempotency_payload_conflict`, `idempotency_tombstone`, `not_found`, `queue_duration_fixed`, `source_unavailable`, `stale_preview` |
| `routines.py` | `historical_edit_forbidden`, `invalid_effective_date`, `invalid_routine`, `not_found`, `occurrence_ended`, `overlap`, `stale_preference_version`, `stale_routine_version`, `template_requires_empty_plan` |
| `weekly_goals.py` | `goal_already_exists`, `goal_not_found`, `goal_version_conflict`, `invalid_goal`, `invalid_week` |

## Superfícies fora do domínio JSON

`GET /healthz/` sem API key: 200 `{status:"ok",database:"ok"}`, 503 `{status:"unavailable",database:"unavailable"}` se SELECT 1 falhar. `/` serve página HTML de perfil; `/admin/` usa autenticação administrativa e suas próprias rotas/formulários. Não são endpoints de integração Pomodoro. Static/media em desenvolvimento dependem de DEBUG.
