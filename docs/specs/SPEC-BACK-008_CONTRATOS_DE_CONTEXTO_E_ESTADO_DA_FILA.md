---
spec_id: SPEC-BACK-008
titulo: Contratos de contexto, posição, saldo e motivo de indisponibilidade da fila
status: DRAFT
fase: TO_BE
situacao: PRONTA_PARA_IMPLEMENTACAO
responsavel: Arquitetura de Software
criado_em: 2026-07-12
atualizado_em: 2026-07-12
dependencias:
  - SPEC-BACK-007
---

# SPEC-BACK-008 — Contratos de contexto, posição, saldo e motivo de indisponibilidade da fila

## 1. Objetivo

Evoluir os contratos HTTP do backend Pomodoro para que clientes mobile e desktop representem canonicamente:

* grupo e modo da execução ativa;
* grupo real da fila, inclusive `Todos`;
* posição real do item;
* limite, consumo e saldo diário do grupo;
* motivo estruturado de fila sem atividade;
* itens expirados, consumidos ou reconciliados;
* execução recuperada em outro dispositivo.

Esta Specification complementa a `SPEC-BACK-007`. Não altera sorteio, elegibilidade, criação, reconciliação ou avanço de filas.

## 2. Diagnóstico confirmado

Foram revalidados `models.py`, `serializers.py`, `views.py`, `urls.py`, `services/`, testes, migrations, Postman e Specifications.

| Contrato | Estado atual | Lacuna |
| --- | --- | --- |
| Próximo item | Retorna fila, modo, tamanho, consumo e `queue_group_id` | Não retorna posição, nome do grupo nem saldo |
| Execução | Retorna IDs, atividade, estado e horários | Não retorna grupo, modo ou bloqueio da fila |
| Fila vazia | 404 com apenas `detail` | Cliente não conhece o motivo |
| Item obsoleto | Códigos genéricos como `queue_item_unavailable` | Não diferencia expirado, consumido ou reconciliado |
| Limite diário | Serviços calculam limite e saldo | Valores não são serializados |

## 3. Escopo e compatibilidade

Inclui ampliar serializers, estruturar fila vazia, estabilizar códigos funcionais, expor métricas diárias e atualizar testes/Postman.

Não inclui alterar regras de fila, limites, autenticação, frontend ou migrations sem necessidade comprovada.

Regras de compatibilidade:

* campos novos são aditivos;
* não remover campos achatados legados;
* manter 201/200 idempotente do início;
* manter 204 de execução ativa ausente;
* manter idempotência do pulo já confirmado;
* manter 404 da fila vazia, adicionando corpo estruturado;
* execuções legadas sem `queue_item` devem serializar contexto como `null`.

## 4. Contrato do item apresentado

Endpoint:

```http
GET /api/activities/next/?group_id={group_id}
```

Resposta 200 esperada:

```json
{
  "queue_item_id": 101,
  "queue_id": 10,
  "queue_group_id": 3,
  "queue_group_name": "Jogos",
  "queue_mode": "normal",
  "skip_locked": false,
  "position": 4,
  "pool_number": 2,
  "pool_size": 18,
  "consumed_count": 3,
  "source_queue_id": null,
  "group_max_daily_minutes": 120,
  "group_consumed_daily_minutes": 75,
  "group_remaining_daily_minutes": 45,
  "state": "presented",
  "activity": {"id": 8, "name": "Path of Exile 2", "duration": 30}
}
```

| Campo | Tipo | Nulável | Semântica |
| --- | --- | ---: | --- |
| `queue_group_id` | int | não | `queue.group_id`, inclusive `Todos` |
| `queue_group_name` | string | não | `queue.group.name` |
| `queue_mode` | string | não | `normal` ou `skipped_review` |
| `skip_locked` | bool | não | fonte canônica do bloqueio |
| `position` | int | não | `ActivityQueueItem.position` |
| `pool_number` | int | não | número do ciclo, não posição |
| `pool_size` | int | não | total de itens |
| `consumed_count` | int | não | consumo conforme regra atual |
| `source_queue_id` | int | sim | fila normal de origem da revisão |
| `group_max_daily_minutes` | int | não | `0` significa ilimitado |
| `group_consumed_daily_minutes` | int | não | minutos reservados no dia |
| `group_remaining_daily_minutes` | int | sim | `null` quando ilimitado |

Para `Todos`, grupo e métricas devem vir do grupo default persistido da fila, nunca do grupo da atividade. O consumo pode ser informativo mesmo sem limite; o saldo é `null`.

## 5. Contrato da execução

Aplicar uniformemente a:

```text
POST /api/activities/{activity_id}/start/
GET  /api/activities/active/
GET  /api/activities/status/{schedule_id}/
POST /api/activities/complete/
GET  /api/activity-executions/{execution_id}/
POST /api/activity-executions/{execution_id}/reconcile/
active_execution incluída em respostas 409
```

Adicionar:

```json
{
  "queue_group_id": 3,
  "queue_group_name": "Jogos",
  "queue_mode": "skipped_review",
  "skip_locked": true,
  "group_max_daily_minutes": 120,
  "group_consumed_daily_minutes": 75,
  "group_remaining_daily_minutes": 45
}
```

A fonte obrigatória é `schedule.queue_item.queue` e `schedule.queue_item.queue.group`. Não usar `schedule.activity.category.group` nem grupo enviado pelo cliente.

Para `Schedule` legado sem item, `queue_id`, `queue_item_id` e todos os campos de contexto são `null`, sem erro 500.

## 6. Fila vazia estruturada

Manter 404 e retornar:

```json
{
  "code": "no_activity_available",
  "detail": "Nenhuma atividade disponível para este grupo.",
  "reason": "group_daily_time_limit_reached",
  "queue_group_id": 3,
  "queue_group_name": "Jogos",
  "group_max_daily_minutes": 120,
  "group_consumed_daily_minutes": 120,
  "group_remaining_daily_minutes": 0
}
```

Motivos permitidos:

```text
no_activities
group_daily_time_limit_reached
no_activity_fits_remaining_time
category_daily_limit_reached
cycle_completed
queue_reconciled
unknown
```

Só retornar motivo específico quando comprovado pelo serviço. Causas ambíguas usam `unknown`. Não classificar como ciclo concluído quando uma nova fila puder ser criada na mesma requisição.

## 7. Códigos de item obsoleto

Ao iniciar ou pular, diferenciar quando possível:

| Código | HTTP | Condição |
| --- | ---: | --- |
| `queue_item_expired` | 409 | item com `state = expired` |
| `queue_item_consumed` | 409 | item concluído ou consumido; pulo repetido continua exceção idempotente |
| `activity_no_longer_eligible` | 409 | atividade inativa ou inelegível antes do início |
| `queue_reconciled` | 409 | expiração identificável por reconciliação |
| `queue_group_mismatch` | 409 | item não corresponde ao grupo explícito quando aplicável |

`queue_item_unavailable` permanece somente como fallback legado ou ambíguo.

Estrutura recomendada:

```json
{
  "code": "queue_item_expired",
  "detail": "O item expirou e a fila deve ser atualizada.",
  "queue_item_id": 101,
  "queue_id": 10,
  "queue_group_id": 3,
  "recoverable": true
}
```

Preservar `skip_locked`, `active_execution_running`, `queue_item_not_found` e a idempotência do pulo já pulado.

## 8. Limites diários

Preservar os códigos reais `daily_limit_reached` e `group_daily_minutes_reached`.

Ampliar o erro de grupo:

```json
{
  "code": "group_daily_minutes_reached",
  "detail": "A atividade não cabe no saldo diário restante do grupo.",
  "queue_group_id": 3,
  "queue_group_name": "Jogos",
  "group_max_daily_minutes": 120,
  "group_consumed_daily_minutes": 105,
  "group_remaining_daily_minutes": 15,
  "activity_duration": 30
}
```

Para `daily_limit_reached`, incluir quando disponível `category_id`, `category_name`, `max_daily_executions` e `started_daily_executions`.

## 9. Diretrizes de implementação

* Reutilizar `group_reserved_minutes()` e `group_remaining_minutes()`; não duplicar cálculo.
* Centralizar contexto compartilhado de fila.
* Usar `select_related('queue_item__queue__group')` nos endpoints de execução.
* Evitar N+1 em polling e conflito com execução aninhada.
* Fazer o serviço de apresentação devolver resultado tipado com item ou motivo, em vez de apenas `ActivityQueueItem | None`.
* Manter a view responsável somente pela tradução para HTTP.
* Não reproduzir queries de elegibilidade na view.

Exemplo conceitual:

```python
QueuePresentationResult(
    item=None,
    reason='group_daily_time_limit_reached',
    group=group,
    consumed_daily_minutes=120,
    remaining_daily_minutes=0,
)
```

## 10. Testes obrigatórios

### 10.1 Item da fila

1. posição real e distinção de ciclo, tamanho e consumo;
2. grupo específico e grupo default em `Todos`;
3. modos normal e revisão;
4. `skip_locked` e origem da revisão;
5. grupo limitado e saldos;
6. grupo ilimitado com saldo `null`.

### 10.2 Execução

1. start, active, status, complete e reconcile retornam contexto idêntico;
2. conflito inclui contexto na execução aninhada;
3. `Todos` não usa grupo da atividade;
4. execução legada sem item usa campos nulos;
5. contagem de queries não cresce por campo.

### 10.3 Fila vazia

1. grupo sem atividades;
2. limite do grupo esgotado;
3. nenhuma atividade cabe no saldo;
4. categorias esgotadas;
5. causa ambígua usa `unknown`;
6. corpo mantém `detail`, adiciona `code` e contexto.

### 10.4 Itens obsoletos

1. expirado, concluído e pulado no start;
2. atividade desativada antes do start;
3. expirado no skip;
4. pulo repetido idempotente;
5. revisão retorna `skip_locked` sem avançar.

Criar preferencialmente `apps/pomodoro/test_spec_back_008.py` e atualizar a collection Postman com assertions para todos os campos e erros.

## 11. Validações

Executar somente com banco de teste isolado:

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test apps.pomodoro
```

Se o ambiente usar Poetry, prefixar com `poetry run`. Nenhum teste pode usar banco compartilhado, desenvolvimento ou produção.

## 12. Critérios de aceite

* [ ] item retorna posição real;
* [ ] grupo da fila é explícito por ID e nome;
* [ ] `Todos` retorna grupo default;
* [ ] todas as respostas de execução possuem contexto idêntico;
* [ ] execução de outro dispositivo é descrita sem estado local;
* [ ] grupo limitado retorna máximo, consumo e saldo;
* [ ] grupo ilimitado retorna saldo `null`;
* [ ] fila vazia possui código e motivo;
* [ ] causas ambíguas não são inventadas;
* [ ] expirado e consumido possuem códigos estáveis;
* [ ] `skip_locked` e pulo idempotente são preservados;
* [ ] não há N+1 nos endpoints de polling;
* [ ] testes passam em banco isolado;
* [ ] Postman e documentação são atualizados;
* [ ] nenhuma regra de fila é alterada fora do escopo.

## 13. Riscos

* Diagnosticar fila vazia pode exigir queries adicionais; encapsular e medir.
* `Schedule.queue_item` é anulável; o serializer não pode quebrar execuções legadas.
* `group_reserved_minutes` considera históricos iniciados; esta Specification apenas expõe a regra atual.
* Alterações de código HTTP ou remoção de campos exigem versionamento e estão fora do escopo.

## 14. Arquivos esperados na implementação futura

```text
apps/pomodoro/serializers.py
apps/pomodoro/views.py
apps/pomodoro/services/activity_queue.py
apps/pomodoro/services/activity_execution.py
apps/pomodoro/test_spec_back_008.py
docs/postman/backend_pomodoro_task.postman_collection.json
docs/specs/SPEC-BACK-008_CONTRATOS_DE_CONTEXTO_E_ESTADO_DA_FILA.md
```

Migrations somente se a implementação demonstrar necessidade real.

## 15. Histórico

| Data | Alteração |
| --- | --- |
| 2026-07-12 | Criação após revalidação das pendências identificadas pela SPEC-FRONT-008. |
