# Contador persistente e integração do frontend

## Status

- Tipo: spec/plano.
- Estado: pronto para implementação após validação do contrato HTTP.
- Escopo: job assíncrono no backend e adequação do frontend Flutter.
- Risco: médio-alto, pois altera a autoridade do estado, o contrato HTTP e o fluxo de conclusão.

## Objetivo

Fazer com que uma atividade iniciada continue válida quando o aplicativo for suspenso, encerrado ou aberto em outro dispositivo. O backend passa a ser a fonte de verdade da execução e conclui a atividade por job assíncrono; o frontend apenas projeta o tempo restante e sincroniza o estado persistido.

## Diagnóstico do fluxo atual

O frontend já calcula o tempo restante por um prazo absoluto (`expected_end_time`), pausa o ticker em segundo plano no Android/iOS e consulta o backend ao retomar. Isso evita depender exclusivamente da quantidade de ticks locais, mas ainda não garante persistência entre dispositivos.

Problemas encontrados:

- o clique em `Start` inicia dois minutos de preparação apenas na memória; o backend só é chamado quando essa fase termina;
- `ActiveActivityStorage` guarda a execução em `SharedPreferences`, portanto outro dispositivo não conhece o `schedule_id` ativo;
- mobile e desktop só procuram uma execução se já houver uma sessão no armazenamento local;
- quando o prazo termina, `CountdownTimer` chama `POST /activities/complete/`; se todos os clientes estiverem fechados, a execução permanece aberta;
- ao abrir o app depois do prazo, as telas também tentam concluir a execução, mantendo o cliente como responsável pelo estado de negócio;
- falhas em `_syncWithBackend()` são descartadas silenciosamente;
- o modelo HTTP é tratado como `Map<String, dynamic>`, espalhando parsing e valores de estado pela UI;
- mobile e desktop duplicam a restauração e a conclusão vencida;
- o endpoint de início pode responder `304` com corpo, contrato inadequado para um `POST` e já coberto pela spec `AJUSTE_CONTRATO_INICIO_CATEGORIA_DEFAULT.md`;
- não existe endpoint para descobrir a execução ativa sem conhecer previamente seu ID;
- a API usa chave de aplicação e não possui identidade de usuário. O backend precisa definir explicitamente o escopo da execução ativa para não misturar sessões de consumidores diferentes.

## Decisões de arquitetura

### Autoridade temporal

O backend é a única autoridade para iniciar, mudar de fase e concluir uma execução. O frontend não deve enviar uma conclusão automática quando o contador visual chegar a zero.

O job não deve decrementar um contador no banco a cada segundo. O desenho esperado é:

1. persistir os prazos absolutos da execução;
2. agendar um único job para `expected_end_at`;
3. calcular `remaining_seconds` a partir de `server_now` e `expected_end_at`;
4. atualizar a tela localmente uma vez por segundo, sem escrita remota;
5. tornar a conclusão idempotente e reconciliar execuções vencidas caso o job atrase ou seja perdido.

### Preparação

A preparação atual de dois minutos será preservada, mas passará a fazer parte da execução persistida. O `POST start` deve ocorrer imediatamente no clique e retornar:

- `state = preparing` enquanto `server_now < starts_at`;
- `state = running` entre `starts_at` e `expected_end_at`;
- `state = completed` depois da conclusão.

O backend define `preparation_seconds`; o cliente não escolhe a duração. Se o produto decidir remover a preparação, o backend retorna `preparation_seconds = 0` e `starts_at = requested_at`, sem exigir um fluxo alternativo no frontend.

### Escopo da execução ativa

Enquanto a aplicação continuar sem autenticação de usuário, a implementação deve adotar e documentar uma execução ativa por identidade de API key. Uma execução global sem escopo só é aceitável se a instalação for formalmente single-tenant e usar uma única chave.

Antes de disponibilizar chaves compartilhadas entre usuários diferentes, `Schedule` deve possuir um proprietário estável. O frontend não deve enviar um identificador de proprietário confiável apenas por convenção local.

### Atualização entre dispositivos

O primeiro dispositivo atualiza a projeção local a cada segundo. Um segundo dispositivo descobre a mesma execução por `GET /activities/active/` ao inicializar, ao retomar o app e no refresh manual.

Enquanto a tela do contador estiver visível, o frontend deve reconciliar com o backend a cada 30 segundos. WebSocket ou SSE não são necessários para a primeira versão. Esse polling não controla o término; ele apenas reduz a defasagem visual quando outro cliente altera o estado.

## Pré-requisitos do backend

### Persistência

`Schedule` ou uma entidade de execução equivalente deve armazenar timestamps completos com timezone, sem reconstruí-los combinando `scheduled_date` e `TimeField`:

- `requested_at`;
- `starts_at`;
- `expected_end_at`;
- `completed_at`, opcional;
- `state`, com valores estáveis;
- `version` ou outro mecanismo para detectar atualizações concorrentes;
- proprietário da execução, conforme o escopo definido.

`History.start_time` deve representar `starts_at`, não o início da preparação. A duração registrada na conclusão deve ser derivada dos prazos persistidos.

### Job assíncrono

O projeto já possui APScheduler e `django-apscheduler`, mas não possui worker configurado. A implementação deve:

- executar o scheduler em processo dedicado, nunca no `AppConfig` nem em cada worker Gunicorn;
- usar job store persistente no PostgreSQL;
- criar um job `date` com ID determinístico, por exemplo `complete-schedule-<id>`;
- agendar o job somente depois do commit da criação da execução;
- usar `replace_existing` ou operação equivalente para manter a inicialização idempotente;
- concluir dentro de transação, com lock da execução e verificação de estado;
- aceitar execução repetida sem duplicar histórico, notificação ou contadores;
- configurar tolerância de atraso (`misfire_grace_time`) e coalescência compatíveis;
- executar reconciliação periódica de execuções vencidas como proteção contra job ausente, restart ou indisponibilidade temporária;
- adicionar um serviço `scheduler` separado ao Compose, com health check e política de restart.

O endpoint de status e o endpoint de execução ativa também devem reconciliar uma execução vencida antes de responder. Assim, o prazo permanece verdadeiro mesmo se o worker estiver momentaneamente atrasado.

### Concorrência

- Dois cliques concorrentes para o mesmo proprietário devem retornar a mesma execução ou um conflito estável, sem criar duas execuções abertas.
- Uma tentativa de iniciar outra atividade enquanto já existe execução ativa deve responder `409 Conflict` com a execução ativa no corpo.
- A conclusão pelo job e uma reconciliação HTTP simultânea devem produzir uma única transição para `completed`.

## Contrato HTTP esperado

Todos os timestamps usam RFC 3339 em UTC, com sufixo `Z`. Todas as respostas temporais incluem `server_now` para o frontend compensar diferença de relógio.

### Iniciar

`POST /api/activities/{activity_id}/start/`

- `201 Created`: execução criada;
- `200 OK`: repetição idempotente da mesma atividade retorna a execução existente;
- `409 Conflict`: existe outra atividade ativa para o mesmo proprietário;
- não retornar `304 Not Modified`.

Resposta:

```json
{
  "schedule_id": 42,
  "state": "preparing",
  "activity": {
    "id": 7,
    "name": "Estudar Python",
    "description": "Revisar testes",
    "duration": 25,
    "category": 2,
    "can_execute": true,
    "remaining_executions": 3
  },
  "requested_at": "2026-06-23T17:00:00Z",
  "starts_at": "2026-06-23T17:02:00Z",
  "expected_end_at": "2026-06-23T17:27:00Z",
  "completed_at": null,
  "preparation_seconds": 120,
  "remaining_seconds": 1620,
  "server_now": "2026-06-23T17:00:00Z",
  "version": 1
}
```

`remaining_seconds` cobre o prazo total até `expected_end_at`. Para exibir apenas a fase atual, o cliente calcula a preparação por `starts_at - now` e a atividade por `expected_end_at - now`.

### Descobrir execução ativa

`GET /api/activities/active/`

- `200 OK` com o mesmo schema quando existe execução `preparing` ou `running`;
- `204 No Content` quando não existe execução ativa.

Esse endpoint é a base da retomada em qualquer dispositivo. Ele não depende de `SharedPreferences` nem de `schedule_id` conhecido pelo cliente.

### Consultar uma execução

`GET /api/activities/status/{schedule_id}/`

Deve retornar o mesmo schema do início. Durante a transição, campos antigos como `expected_end_time` e `is_completed` podem ser mantidos, mas o frontend novo consome `state` e `expected_end_at`.

### Conclusão manual

O frontend não chama mais `POST /activities/complete/` ao chegar a zero. Se o produto ainda precisar do botão “concluir agora”, ele deve possuir semântica separada e explícita; conclusão automática e interrupção antecipada não podem compartilhar uma operação ambígua.

### Erros

Erros devem usar um formato estável:

```json
{
  "code": "active_schedule_conflict",
  "detail": "Já existe uma atividade em execução.",
  "active_schedule": { "schedule_id": 42, "state": "running" }
}
```

O frontend decide o tratamento por `status code` e `code`, nunca pelo texto de `detail`.

## Estado no frontend

Criar um modelo tipado `ActivityExecution` e um controlador único para mobile e desktop.

Estados de apresentação:

| Estado | Comportamento |
| --- | --- |
| `initialLoading` | Busca grupos e execução ativa; mostra loading sem habilitar `Start`. |
| `idle` | Não existe execução ativa; mostra próxima atividade e habilita `Start`. |
| `starting` | `POST start` em andamento; bloqueia cliques repetidos. |
| `preparing` | Exibe “Preparação” até `starts_at`. |
| `running` | Exibe “Tempo restante” até `expected_end_at`. |
| `reconciling` | Mantém o último tempo projetado e indica sincronização discreta. |
| `completed` | Cancela ticker/notificação local, atualiza histórico e busca próxima atividade. |
| `offlineWithSnapshot` | Exibe snapshot como potencialmente desatualizado e tenta reconectar; não conclui localmente. |
| `error` | Mostra erro recuperável e ação de tentar novamente. |

Transições principais:

```text
bootstrap/resume -> GET active -> idle | preparing | running
idle -> Start -> starting -> preparing | running
preparing -> starts_at alcançado -> running
running -> expected_end_at alcançado -> reconciling -> completed
poll/resume -> estado devolvido pelo backend
falha de rede com snapshot -> offlineWithSnapshot -> reconciling
```

Quando o prazo local chegar a zero, a UI congela em `00:00`, entra em `reconciling` e consulta o backend. Ela só avança para a próxima atividade após receber `completed` ou após `GET active` retornar `204` e atualizar o histórico.

## Sincronização de relógio

O cliente deve estimar o desvio entre relógios em cada resposta:

```text
estimatedServerNow = server_now + tempo_monotônico_desde_a_resposta
remaining = deadline - estimatedServerNow
```

Para reduzir o efeito da latência, pode usar o ponto médio entre o instante local de envio e recebimento. O ticker usa fonte monotônica entre sincronizações; `DateTime.now()` não deve ser a única referência porque o usuário pode alterar o relógio do aparelho.

Ao receber nova resposta, corrigir imediatamente diferenças pequenas. Diferenças visíveis maiores que dois segundos podem ser atualizadas de uma vez, pois a exatidão do servidor é prioritária.

## Armazenamento local e notificações

`ActiveActivityStorage` deixa de ser fonte de verdade. Há duas opções válidas:

1. removê-lo e sempre usar `GET active` no bootstrap;
2. mantê-lo apenas como cache de último snapshot para UX offline, sempre sobrescrito pela resposta do backend.

A opção recomendada é manter um snapshot mínimo e tipado, sem serializar uma cópia completa de `Activity`. Um snapshot nunca autoriza conclusão, início ou troca de atividade.

Notificação local continua sendo melhor esforço no dispositivo que recebeu a execução. O job no backend, sozinho, não entrega push em aparelhos fechados. Notificação confiável em todos os dispositivos exige cadastro de device tokens e FCM/APNs/Web Push, fora da primeira implementação. Ao sincronizar uma conclusão, o app não deve emitir novamente uma notificação local já agendada.

## Organização prevista no frontend

| Arquivo | Alteração planejada |
| --- | --- |
| `lib/models/activity_execution.dart` | Criar modelo tipado, enum de estado e parsing de timestamps UTC. |
| `lib/services/api_service.dart` | Fazer `startActivity` retornar a execução completa; adicionar `getActiveExecution`; tipar `getExecutionStatus`; mapear `204`, `409` e erros estáveis; remover `304` como sucesso. |
| `lib/services/active_activity_storage.dart` | Remover ou converter em cache de snapshot sem autoridade. |
| `lib/controllers/pomodoro_controller.dart` | Centralizar bootstrap, start, ticker visual, polling, lifecycle, reconciliação, clock offset e erros. |
| `lib/widgets/countdown_timer.dart` | Tornar widget de apresentação; remover chamadas HTTP, persistência e conclusão. |
| `lib/screens/home_mobile.dart` | Consumir o controlador compartilhado e remover `_resolveInitialActivity`. |
| `lib/screens/home_desktop.dart` | Consumir o mesmo controlador e remover lógica duplicada. |
| `lib/services/notification_service.dart` | Reagendar/cancelar por `schedule_id` sem emitir duplicidades durante reconciliação. |
| `test/models/activity_execution_test.dart` | Cobrir parsing, UTC, estados e compatibilidade do contrato. |
| `test/controllers/pomodoro_controller_test.dart` | Cobrir transições, polling, lifecycle, offline, clock skew e concorrência de start. |
| `test/widgets/countdown_timer_test.dart` | Cobrir labels, progresso, loading, erro e zero aguardando reconciliação. |

Se o projeto não usar injeção de dependência, o controlador deve ao menos receber interfaces de API, relógio, armazenamento e notificações pelo construtor para permitir testes determinísticos.

## Plano de implementação

### Fase 1 — backend e contrato

1. Corrigir o retorno `304` do início conforme a spec já existente.
2. Definir o proprietário e garantir uma única execução ativa por proprietário no banco.
3. Migrar prazos para campos `DateTimeField` com timezone e adicionar estados explícitos.
4. Isolar início, conclusão e reconciliação em services transacionais.
5. Criar serializers de execução com schema único.
6. Implementar `GET active` e adaptar `start`/`status`.
7. Criar o worker APScheduler dedicado, job idempotente e reconciliação periódica.
8. Cobrir restart do scheduler, job duplicado, job atrasado e chamadas concorrentes.

### Fase 2 — frontend tipado

1. Adicionar `ActivityExecution` e testes de contrato.
2. Atualizar `ApiService` para o novo schema sem usar mapas na UI.
3. Criar o controlador e testes com relógio falso.
4. Mover a preparação para os prazos retornados pelo backend.
5. Transformar `CountdownTimer` em apresentação pura.
6. Integrar o controlador em mobile e desktop.
7. Rebaixar `SharedPreferences` para cache e tratar offline explicitamente.
8. Ajustar notificações locais e remover conclusão disparada pelo cliente.

### Fase 3 — rollout

1. Publicar primeiro um backend compatível com o frontend atual nos endpoints antigos.
2. Validar jobs e reconciliação em ambiente controlado.
3. Publicar o frontend novo consumindo `GET active` e o schema tipado.
4. Observar jobs pendentes, atraso de conclusão, conflitos e taxa de polling.
5. Remover campos e comportamento legados somente após os clientes suportados migrarem.

## Testes obrigatórios

### Backend

- início cria prazos corretos incluindo preparação;
- repetição idempotente não duplica `Schedule`, `History` ou job;
- atividade diferente durante execução ativa retorna `409` e a execução existente;
- job no prazo conclui uma única vez;
- job repetido e reconciliação concorrente são idempotentes;
- execução vencida é concluída por `GET active`/`status` mesmo sem o job;
- restart do scheduler recupera jobs persistidos ou a reconciliação conclui vencidos;
- `GET active` encontra a execução em outra requisição com o mesmo proprietário;
- proprietários diferentes não acessam a execução um do outro;
- timestamps são UTC e `server_now` está presente;
- testes usam exclusivamente banco isolado.

### Frontend

- clicar em `Start` chama o backend imediatamente e apenas uma vez;
- resposta `preparing` mostra o prazo retornado, sem iniciar preparação local independente;
- fechar e reabrir sem snapshot local restaura via `GET active`;
- outro dispositivo com o mesmo proprietário restaura a mesma execução;
- app em background não perde o prazo e reconcilia ao retomar;
- relógio local adiantado ou atrasado não muda a conclusão de negócio;
- chegar a `00:00` não chama `completeActivity`;
- `204` leva ao estado ocioso e atualiza histórico/próxima atividade;
- `409` passa a exibir a execução já ativa;
- falha de rede mantém snapshot sinalizado e nunca conclui localmente;
- polling é cancelado no `dispose` e não cria timers duplicados;
- mobile e desktop apresentam os mesmos estados.

## Validações

Backend, somente com banco de teste isolado:

```bash
APP_ENV=test .venv/bin/python manage.py check --settings=config.settings.test
APP_ENV=test .venv/bin/python manage.py makemigrations --check --dry-run --settings=config.settings.test
APP_ENV=test .venv/bin/python manage.py test --settings=config.settings.test
```

Frontend:

```bash
dart format --output=none --set-exit-if-changed lib test
flutter analyze
flutter test
```

Teste manual mínimo em dois dispositivos:

1. iniciar no dispositivo A e confirmar `preparing` imediatamente;
2. abrir no dispositivo B sem armazenamento local e confirmar a mesma execução;
3. encerrar os dois apps antes do prazo;
4. aguardar o job e confirmar no banco uma única conclusão;
5. reabrir B e confirmar histórico atualizado e ausência de execução ativa.

## Critérios de aceite

- a requisição de início ocorre no clique, antes da preparação;
- encerrar todos os clientes não impede a conclusão;
- um dispositivo sem cache local descobre a execução ativa;
- somente o backend muda o estado persistido para `completed` automaticamente;
- nenhum componente Flutter chama conclusão ao zerar o contador;
- preparação, execução e conclusão usam prazos retornados pelo servidor;
- job e reconciliação são idempotentes e resistentes a restart;
- mobile e desktop compartilham a mesma lógica de estado;
- loading, erro, offline, conflito e conclusão possuem apresentação definida;
- testes de contrato, concorrência, lifecycle e clock skew passam.

## Riscos e mitigação

| Risco | Mitigação |
| --- | --- |
| Scheduler iniciado por cada worker web | Processo dedicado e proibição explícita de bootstrap no `AppConfig`. |
| Job perdido entre commit e agendamento | Reconciliação periódica e reconciliação nos endpoints de leitura. |
| Duas execuções abertas por corrida | Constraint de banco por proprietário e service transacional com lock. |
| Relógio do aparelho incorreto | `server_now`, prazos UTC e fonte monotônica no cliente. |
| Polling excessivo | Intervalo de 30 segundos apenas com contador visível e sync imediato em lifecycle/ações. |
| Sessões misturadas entre usuários | Proprietário persistido; não confiar em ID arbitrário enviado pelo cliente. |
| Notificação duplicada | ID determinístico por execução e separação entre notificação agendada e estado sincronizado. |
| Frontend antigo concluir antes do job | Backend mantém operação idempotente durante rollout e registra origem da transição. |
| Alteração do schema quebrar clientes | Rollout backend-first com período de compatibilidade. |

## Fora de escopo

- implementar as mudanças desta spec;
- sincronização em tempo real por WebSocket ou SSE;
- entrega push por FCM, APNs ou Web Push;
- pausa e retomada de uma execução;
- múltiplas execuções simultâneas para o mesmo proprietário;
- autenticação completa de usuários;
- alterar duração, preparação ou atividade depois do início.
