# Atividade ativa persistente multiplataforma

## Status

- Tipo: spec/plano.
- Estado: dependente de `docs/specs/FILA_RANDOMIZACAO_ATIVIDADES.md`.
- Ordem de desenvolvimento: implementar primeiro `docs/specs/FILA_RANDOMIZACAO_ATIVIDADES.md`; implementar esta spec depois.
- Escopo: backend Django/DRF e contrato de sincronizacao usado por mobile e desktop.
- Risco: medio-alto, pois muda a autoridade do contador, adiciona estado ativo persistente e precisa evitar duas execucoes simultaneas entre clientes.

## Objetivo

Garantir que a atividade ativa seja a mesma no mobile e no desktop. Se uma atividade for iniciada em um cliente, outro cliente deve descobrir essa execucao ativa, exibir a mesma contagem regressiva e continuar a partir da mesma hora inicial persistida no backend.

O backend deve gravar a hora inicial da execucao e ser a fonte de verdade do tempo restante. Os clientes podem projetar a contagem localmente para fluidez visual, mas devem reconciliar com o backend a cada 5 minutos para revisar e corrigir a contagem regressiva com base na hora inicial persistida.

## Dependencia com fila de atividades

Esta spec depende da fila persistida definida em `docs/specs/FILA_RANDOMIZACAO_ATIVIDADES.md`.

A implementacao da fila deve vir antes porque:

- ela define `queue_id` e `queue_item_id`, necessarios para identificar a atividade ativa dentro da ordem sorteada;
- ela estabelece estados de item como `presented`, `started`, `completed`, `skipped` e `expired`;
- ela define regras de concorrencia para nao consumir ou concluir itens duplicados;
- ela cria o contrato em que `POST start` recebe `queue_item_id`;
- ela evita que um refresh, mobile ou desktop recebam atividades diferentes durante a mesma sessao.

Sem a fila, a atividade ativa persistente pode ate preservar o contador, mas ainda pode ficar desalinhada da "proxima atividade" sorteada em outro cliente.

## Diagnostico do problema

O comportamento desejado nao deve depender de armazenamento local do cliente. Se o mobile iniciar uma atividade e o desktop for aberto depois, o desktop precisa consultar o backend e receber:

- qual atividade esta ativa;
- qual item da fila originou essa execucao;
- quando a execucao iniciou;
- quando deve terminar;
- quanto tempo resta segundo o relogio do servidor;
- qual versao do estado esta vigente.

O mesmo vale no sentido inverso: uma atividade iniciada no desktop deve aparecer no mobile.

## Decisoes de arquitetura

### Backend como autoridade temporal

O backend deve persistir timestamps absolutos e calcular o tempo restante a partir deles. Nao deve existir contador decrementado no banco a cada segundo.

Campos minimos na execucao ativa:

| Campo | Descricao |
| --- | --- |
| `queue_id` | Fila ativa que originou a execucao. |
| `queue_item_id` | Item da fila em execucao. |
| `activity_id` | Atividade executada. |
| `started_at` | Hora inicial persistida pelo backend. |
| `expected_end_at` | Prazo final calculado pelo backend. |
| `completed_at` | Hora de conclusao, quando houver. |
| `state` | Estado estavel da execucao. |
| `version` | Inteiro para controle de concorrencia e cache. |
| `scope_key` | Escopo da execucao ativa enquanto nao houver usuario autenticado. |

`started_at` deve ser gravado no momento em que a execucao passa a contar como ativa. Se a preparacao de dois minutos continuar existindo, a implementacao deve decidir entre:

- manter `requested_at`, `starts_at` e `expected_end_at`, onde `starts_at` e a hora inicial da atividade real;
- ou tratar a preparacao como parte da execucao ativa e usar `started_at` como inicio do ciclo completo.

A decisao precisa ser unica no backend e refletida no contrato HTTP.

### Reconciliacao a cada 5 minutos

Enquanto houver atividade ativa aberta, o cliente deve consultar o backend a cada 5 minutos. Essa consulta nao serve para decrementar tempo; ela serve para corrigir deriva de relogio, atualizar estado e detectar conclusao feita por outro cliente ou job.

Regra:

```text
remaining_seconds = max(0, expected_end_at - server_now)
```

O backend deve devolver `server_now` em todas as respostas temporais. O cliente calcula a projecao local entre uma resposta e outra usando tempo monotonico local, mas substitui a projecao pelo valor do backend na reconciliacao.

### Retomada entre mobile e desktop

Todo cliente deve consultar a execucao ativa:

- ao abrir o app;
- ao retomar do background;
- ao trocar de tela para o contador;
- a cada 5 minutos enquanto o contador estiver visivel;
- apos erro `409 active_execution_conflict` no start;
- apos qualquer erro de mismatch entre atividade local e item da fila.

O cliente nao deve depender de `SharedPreferences`, cache local ou `schedule_id` salvo para descobrir uma execucao ativa.

### Escopo da execucao ativa

Enquanto a API nao possuir usuario autenticado, a implementacao deve declarar um escopo estavel. A recomendacao inicial e uma execucao ativa por API key.

Uma execucao global unica so e aceitavel se a instalacao for formalmente single-tenant. Se houver multiplos consumidores reais usando a mesma API key, esta spec exige criar ou planejar um proprietario antes de liberar sincronizacao multiplataforma.

## Modelo de dados previsto

Pode ser uma evolucao de `Schedule` ou uma entidade dedicada, desde que o contrato seja claro. A recomendacao e criar ou adaptar uma entidade de execucao ativa com constraint de unicidade por escopo e estado aberto.

Estados sugeridos:

| Estado | Significado |
| --- | --- |
| `running` | Atividade em execucao e ainda nao vencida. |
| `completed` | Atividade concluida pelo job, reconciliacao ou acao explicita permitida. |
| `cancelled` | Execucao cancelada por regra futura, fora do fluxo normal. |
| `expired` | Estado tecnico para execucao vencida que ainda precisa reconciliar, se necessario. |

Se a preparacao for mantida como estado persistido, incluir tambem `preparing`.

Constraints esperadas:

- apenas uma execucao em `preparing` ou `running` por `scope_key`;
- `queue_item_id` unico para execucoes nao canceladas;
- transicoes idempotentes para `completed`;
- `started_at` e `expected_end_at` obrigatorios para execucoes abertas;
- `expected_end_at` sempre maior que `started_at`.

## Servico de dominio

Adicionar uma camada de servico para centralizar a regra temporal:

| Arquivo | Responsabilidade |
| --- | --- |
| `apps/pomodoro/services/activity_execution.py` | Iniciar, descobrir, reconciliar e concluir execucoes ativas. |
| `apps/pomodoro/services/activity_queue.py` | Continuar responsavel por fila, consumo e transicoes do `queue_item_id`. |
| `apps/pomodoro/repositories/activity_execution.py` | Encapsular queries com lock e constraints, se a complexidade justificar. |

A view deve coordenar request, permissao e response. Calculo temporal, idempotencia e transicoes devem ficar no service.

## Contrato HTTP esperado

Todos os timestamps devem usar RFC 3339 em UTC, com sufixo `Z`. Toda resposta temporal deve incluir `server_now`.

### Iniciar atividade

`POST /api/activities/{activity_id}/start/`

Payload:

```json
{
  "queue_item_id": 91,
  "group_id": 1
}
```

Respostas:

- `201 Created`: execucao criada;
- `200 OK`: repeticao idempotente da mesma atividade e mesmo item retorna a execucao existente;
- `409 Conflict`: ja existe outra execucao ativa no mesmo escopo;
- `409 Conflict`: `queue_item_id` nao corresponde a atividade solicitada.

Resposta de sucesso:

```json
{
  "execution_id": 42,
  "queue_id": 10,
  "queue_item_id": 91,
  "state": "running",
  "activity": {
    "id": 7,
    "name": "Estudar Python",
    "duration": 25,
    "category": 2,
    "group_id": 1
  },
  "started_at": "2026-07-07T13:00:00Z",
  "expected_end_at": "2026-07-07T13:25:00Z",
  "completed_at": null,
  "remaining_seconds": 1500,
  "server_now": "2026-07-07T13:00:00Z",
  "version": 1
}
```

Erro por execucao ativa diferente:

```json
{
  "code": "active_execution_conflict",
  "detail": "Ja existe uma atividade em execucao.",
  "active_execution": {
    "execution_id": 42,
    "queue_item_id": 91,
    "state": "running"
  }
}
```

### Descobrir atividade ativa

`GET /api/activities/active/`

Respostas:

- `200 OK`: existe execucao ativa;
- `204 No Content`: nao existe execucao ativa.

Antes de responder, o backend deve reconciliar execucoes vencidas no mesmo escopo. Se `expected_end_at <= server_now`, a resposta deve refletir o estado concluido ou retornar `204`, conforme a decisao de produto para historico imediato.

### Consultar status de uma execucao

`GET /api/activity-executions/{execution_id}/`

Retorna o mesmo schema de sucesso do start, com `remaining_seconds` recalculado no momento da resposta.

### Reconciliar explicitamente

`POST /api/activity-executions/{execution_id}/reconcile/`

Uso esperado pelo frontend a cada 5 minutos e ao retomar o app.

Resposta:

- `200 OK` com execucao atualizada;
- `404 Not Found` se a execucao nao pertence ao escopo atual;
- `409 Conflict` se o cliente enviou `version` antiga e a execucao ja mudou de forma relevante.

Payload opcional:

```json
{
  "known_version": 1
}
```

O backend pode optar por usar apenas `GET status` para reconciliacao. Se fizer isso, a implementacao deve documentar que `GET status` e `GET active` tambem executam reconciliacao vencida antes de responder.

### Conclusao automatica

A conclusao automatica deve ser feita pelo backend:

- por job agendado para `expected_end_at`, se houver scheduler configurado;
- por reconciliacao HTTP quando qualquer cliente consultar uma execucao vencida;
- por rotina periodica de varredura como protecao contra job perdido.

O cliente nao deve ser a autoridade para concluir a atividade quando o contador visual chega a zero.

## Integracao com a fila

Ao iniciar:

1. validar se `queue_item_id` pertence a fila ativa do escopo;
2. validar se o item aponta para `activity_id`;
3. transicionar o item para `started`;
4. criar execucao ativa com `started_at` do servidor;
5. retornar schema temporal.

Ao concluir:

1. bloquear a execucao ativa em transacao;
2. marcar a execucao como `completed`;
3. marcar o `ActivityQueueItem` como `completed`;
4. criar ou atualizar `History`/`Schedule` conforme o modelo atual;
5. registrar evento positivo da fila, conforme `FILA_RANDOMIZACAO_ATIVIDADES.md`;
6. manter a operacao idempotente para chamadas repetidas.

Ao pular:

- uma atividade em `running` nao deve poder ser pulada pelo endpoint de fila;
- se o produto permitir cancelar atividade ativa, isso deve ser endpoint separado e fora desta spec;
- `POST skip` deve responder `409 active_execution_running` quando o item ja estiver em execucao.

## Comportamento esperado no frontend

O frontend deve tratar o backend como fonte de verdade.

Fluxo de inicializacao:

```text
abrir app -> GET /api/activities/active/ -> running | idle
running -> abrir tela de contador com dados do backend
idle -> buscar proxima atividade pela fila
```

Fluxo de reconciliacao:

```text
contador visivel -> projetar localmente por tempo monotonico
a cada 5 minutos -> reconciliar com backend
se remaining_seconds divergir -> ajustar UI para valor do backend
se state completed -> fechar contador e buscar proxima atividade
```

Se o cliente ficar offline, ele pode continuar exibindo a ultima projecao marcada como potencialmente desatualizada, mas nao deve concluir a atividade localmente. Ao reconectar, deve reconciliar antes de permitir nova atividade.

## Testes esperados

### Backend

- iniciar atividade grava `started_at`, `expected_end_at`, `queue_item_id` e `scope_key`;
- `GET active` em outro cliente/sem cache retorna a mesma execucao ativa;
- `remaining_seconds` e calculado por `expected_end_at - server_now`;
- reconciliacao apos 5 minutos corrige o tempo restante sem alterar `started_at`;
- duas chamadas concorrentes de start nao criam duas execucoes abertas no mesmo escopo;
- start de outra atividade durante execucao ativa retorna `409 active_execution_conflict`;
- start com `queue_item_id` divergente retorna `409 queue_item_mismatch`;
- execucao vencida e concluida de forma idempotente por reconciliacao;
- conclusao atualiza o item da fila para `completed`;
- endpoint de pular retorna conflito quando o item ja esta em execucao.

### Frontend

- mobile inicia e desktop restaura a mesma execucao por `GET active`;
- desktop inicia e mobile restaura a mesma execucao por `GET active`;
- refresh da tela nao reinicia contador;
- app retomado do background chama reconciliacao antes de exibir estado definitivo;
- polling de 5 minutos ajusta a UI quando o relogio local diverge;
- ao receber `completed`, o cliente busca a proxima atividade pela fila.

## Validacoes manuais

1. Implementar e popular uma fila conforme `FILA_RANDOMIZACAO_ATIVIDADES.md`.
2. Iniciar uma atividade no mobile.
3. Abrir o desktop e confirmar que a mesma atividade e o mesmo `queue_item_id` aparecem.
4. Aguardar mais de 5 minutos e confirmar que a reconciliacao ajusta o tempo pelo backend.
5. Repetir o fluxo iniciando pelo desktop e retomando no mobile.
6. Fechar todos os clientes ate depois de `expected_end_at` e confirmar que o backend conclui/reconcilia a execucao.

## Riscos

| Risco | Mitigacao |
| --- | --- |
| Fila e execucao ativa ficarem fora de sincronia | Implementar esta spec somente depois da fila e exigir `queue_item_id` no start. |
| Relogios diferentes entre clientes | Responder `server_now` e reconciliar a cada 5 minutos. |
| Dois starts simultaneos criarem execucoes duplicadas | Constraint por `scope_key` e transacao com lock. |
| Cliente concluir atividade sozinho | Remover conclusao automatica local e reconciliar com backend. |
| API key compartilhada misturar usuarios | Declarar escopo por API key ou criar proprietario antes de liberar multiusuario. |
| Job de conclusao falhar | Reconciliar execucoes vencidas em `GET active`, `GET status` e rotina periodica. |

## Fora de escopo

- Sincronizacao em tempo real por WebSocket ou SSE.
- Criar autenticacao de usuario completa.
- Redesenhar toda a gamificacao de favoritas e puladas.
- Permitir cancelar atividade ativa.
- Trocar framework, banco ou gerenciador de dependencias.

## Decisoes pendentes

1. Confirmar se a preparacao de dois minutos continua existindo e como ela aparece no contrato temporal.
2. Confirmar se o escopo inicial sera API key ou instalacao single-tenant.
3. Definir se `GET status` sera suficiente para reconciliacao ou se existira `POST reconcile`.
4. Definir se execucao vencida em `GET active` retorna `completed` uma vez ou ja retorna `204 No Content`.
5. Definir se a entidade principal sera evolucao de `Schedule` ou um novo modelo de execucao.
