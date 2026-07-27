# SPEC-BACK-011 — Recriação manual e listagem resumida da fila

## 1. Status

Planejada em 2026-07-27.

Esta Specification descreve somente a mudança do backend. A inclusão ou renomeação do
botão no frontend deve ser planejada no repositório Flutter depois que o contrato HTTP
estiver implementado.

## 2. Objetivo

Permitir que o usuário solicite explicitamente uma nova fila aleatória para o grupo
selecionado e consulte as próximas 30 atividades dessa fila com indicadores históricos.

A funcionalidade deve:

- recriar somente a fila do `scope_key` e do grupo informados;
- preservar o histórico completo da fila substituída;
- recolocar na nova fila as atividades puladas na fila normal ativa, quando continuarem
  elegíveis;
- continuar registrando cada novo pulo para relatórios;
- manter a randomização ponderada e a prioridade premium vigentes;
- disponibilizar uma rota de leitura com no máximo 30 itens, sem limitar a fila total a
  30 atividades.

## 3. Estado atual confirmado

O backend Django/DRF já possui:

- uma fila ativa por `(scope_key, group_id)`;
- filas independentes para grupos específicos e para o grupo agregador `Todos`;
- ordem aleatória persistida em `ActivityQueueItem.position`;
- fila normal contendo todas as atividades elegíveis, inclusive quando existem mais de
  30;
- retorno estável do item `presented` nas chamadas repetidas de `GET /activities/next/`;
- pulo persistido em `ActivityQueueItem.state`, `skipped_at` e
  `ActivityPreferenceEvent(event_type=skipped)`;
- revisão obrigatória `skipped_review` criada quando uma fila normal termina com itens
  pulados;
- `History` criado no início e concluído no término de uma execução.

O frontend atual apenas recarrega `GET /api/activities/next/`. Essa operação não recria a
fila e, corretamente, devolve o mesmo item apresentado. Portanto, o novo botão não deve
reutilizar a ação de recarga existente.

O campo `Activity.last_executed` não é atualizado pelo fluxo atual. A nova listagem não
deve usá-lo como fonte de verdade; a última execução deve ser calculada a partir de
`History`.

## 4. Escopo

### 4.1 Incluído

- endpoint transacional de recriação manual;
- serviço de domínio para substituir uma fila normal ativa;
- vínculo auditável entre a fila nova e a fila substituída;
- preservação dos registros de pulo;
- listagem dos primeiros 30 itens operacionais da fila ativa;
- agregação de execuções concluídas, última execução e pulos;
- testes de serviço, API, concorrência e isolamento por grupo;
- migration aditiva necessária à rastreabilidade.

### 4.2 Fora do escopo

- implementar o botão no Flutter;
- apagar ou compactar filas antigas;
- transformar a fila completa em uma fila fixa de 30 itens;
- mudar os pesos atuais da randomização;
- criar um novo modelo específico de relatório;
- alterar o endpoint vigente de próxima atividade;
- permitir pulo dentro de uma fila `skipped_review`;
- corrigir retroativamente dados históricos inconsistentes.

## 5. Decisões funcionais

### 5.1 Recriação é diferente de recarga

Recarregar continua sendo uma leitura do estado persistido.

Recriar é uma ação explícita que:

1. encerra a fila normal ativa selecionada como `cancelled`;
2. preserva seus itens e eventos;
3. gera uma nova fila normal;
4. calcula uma nova ordem aleatória;
5. retorna a identidade da nova fila.

A operação nunca deve apagar fisicamente `ActivityQueue`, `ActivityQueueItem`,
`ActivityPreferenceEvent`, `Schedule` ou `History`.

### 5.2 Escopo e grupo

A recriação deve afetar exatamente:

```text
scope_key derivado da API key + group_id selecionado
```

Recriar o Grupo A não pode alterar a fila do Grupo B nem a fila de `Todos`.

Quando `group_id` não for enviado, aplica-se o contrato vigente e o grupo
`is_default=True` é utilizado.

O nome textual `Todos` não deve ser usado para identificar o grupo agregador.

### 5.3 Fila que pode ser recriada

Somente uma fila com:

```text
state = active
mode = normal
```

pode ser recriada.

Uma fila `skipped_review` ativa deve retornar conflito `queue_recreation_locked`. A
recriação manual não pode ser usada para escapar da revisão obrigatória definida pela
`SPEC-BACK-007`.

Se existir execução `preparing` ou `running` no escopo, a operação deve retornar
`active_execution_running`. A execução não pode ser cancelada ou desvinculada
implicitamente.

### 5.4 Atividades puladas

Antes de encerrar a fila, o serviço deve identificar os itens com:

```text
state = skipped
```

na própria fila ativa.

Cada atividade pulada deve participar uma única vez do conjunto candidato da nova fila,
desde que ainda obedeça às regras de elegibilidade no instante da recriação:

- atividade ativa;
- categoria válida;
- pertencimento ao grupo, respeitando a semântica agregadora de `Todos`;
- limite diário da categoria;
- limite diário de minutos do grupo;
- regra de atividade já executada no dia;
- ausência de execução incompatível;
- demais regras vigentes de premium e elegibilidade.

Uma atividade pulada que deixou de ser elegível não deve violar limites para entrar na
fila. A resposta deve informar sua ausência por meio de `skipped_not_requeued`, contendo
`activity_id` e um `reason` estável.

Razões mínimas:

```text
inactive
category_unavailable
group_mismatch
category_daily_limit_reached
group_daily_minutes_reached
already_completed_today
active_execution_conflict
```

Os itens pulados elegíveis entram no mesmo embaralhamento das demais atividades. Eles não
devem ser fixados no início nem no fim da fila e não recebem peso novo nesta spec.

### 5.5 Conjunto e ordem da nova fila

A nova fila normal deve conter todas as atividades elegíveis do grupo, não apenas 30.

O conjunto é conceitualmente:

```text
atividades elegíveis atuais
UNION
atividades puladas elegíveis da fila substituída
```

A união deve ser deduplicada por `activity_id`.

A ordem deve continuar usando a regra de `_weighted_order()`:

- premium vigente antes de atividade normal;
- ponderação histórica já existente;
- randomização dentro dos conjuntos;
- posição persistida uma única vez.

O serviço de randomização deve aceitar uma fonte pseudoaleatória injetável nos testes.

### 5.6 Encerramento da fila substituída

Depois que todas as pré-condições e o novo conjunto forem validados:

- `pending` e `presented` sem execução devem mudar para `expired`;
- `completed`, `skipped` e `expired` devem permanecer inalterados;
- um item `started` impede a recriação enquanto sua execução estiver aberta;
- a fila anterior deve mudar para `state=cancelled`;
- `closed_at` deve ser preenchido;
- `consumed_count` e `pool_size` devem permanecer auditáveis;
- `finalize_queue_if_finished()` não deve ser chamado para criar uma revisão da fila
  cancelada manualmente.

A fila nova deve usar:

```text
state = active
mode = normal
skip_locked = false
pool_number = próximo número do mesmo scope_key e grupo
```

Se a criação da nova fila falhar, a transação deve reverter também a expiração e o
cancelamento da fila anterior.

### 5.7 Histórico de pulos

O histórico para relatórios continua sendo
`ActivityPreferenceEvent(event_type=skipped)`, vinculado ao item, à fila e à atividade.

A recriação:

- não cria um novo evento de pulo para eventos antigos;
- não transforma `skipped` em `expired`;
- não remove eventos;
- não marca um pulo antigo como concluído;
- cria novos `ActivityQueueItem` para as ocorrências da fila nova;
- permite que um novo pulo da mesma atividade gere uma nova ocorrência histórica.

Assim, `skip_count` representa ações de pulo, e não a quantidade de atividades distintas
que já foram puladas.

## 6. Alteração de modelo

Adicionar a `ActivityQueue` um vínculo opcional e auditável:

```python
recreated_from = models.OneToOneField(
    'self',
    on_delete=models.PROTECT,
    related_name='recreated_queue',
    null=True,
    blank=True,
)
```

Regras:

- filas comuns e revisões automáticas usam `recreated_from=NULL`;
- a fila criada manualmente aponta para a fila cancelada;
- uma fila cancelada pode originar no máximo uma recriação direta;
- `source_queue` permanece exclusivo à relação entre revisão e fila normal;
- `recreated_from` não deve reutilizar nem mudar a semântica de `source_queue`.

A migration deve ser aditiva, aceitar `NULL` e não atualizar ou apagar filas existentes.

## 7. Endpoint de recriação

### 7.1 Contrato

```http
POST /api/activity-queue/recreate/
Authorization: Api-Key ...
Content-Type: application/json
```

Payload:

```json
{
  "group_id": 3,
  "expected_queue_id": 10
}
```

`expected_queue_id` é obrigatório para evitar que clique duplo ou resposta atrasada
recrie uma fila que o cliente ainda não conhece.

### 7.2 Resposta de sucesso

```http
201 Created
```

```json
{
  "queue_id": 11,
  "recreated_from_queue_id": 10,
  "queue_group_id": 3,
  "queue_group_name": "Jogos",
  "queue_mode": "normal",
  "pool_number": 4,
  "pool_size": 48,
  "skip_locked": false,
  "requeued_skipped_count": 2,
  "skipped_not_requeued": []
}
```

`pool_size` informa o tamanho total real da fila e pode ser maior que 30.

### 7.3 Erros funcionais

| HTTP | `code` | Condição |
| --- | --- | --- |
| 400 | `expected_queue_id_required` | Identificador esperado não enviado. |
| 404 | `group_not_found` | Grupo informado não existe. |
| 404 | `active_queue_not_found` | Não existe fila ativa para o escopo e grupo. |
| 409 | `queue_changed` | A fila ativa atual difere de `expected_queue_id`. |
| 409 | `queue_recreation_locked` | A fila ativa é uma revisão obrigatória. |
| 409 | `active_execution_running` | Existe execução aberta no escopo. |
| 409 | `no_activity_available` | Nenhuma atividade pode compor a nova fila. |
| 500 | `queue_recreation_failed` | Falha inesperada com rollback integral. |

Erros `409` devem informar, quando disponível:

```json
{
  "queue_id": 10,
  "queue_group_id": 3,
  "recoverable": true
}
```

### 7.4 Idempotência por estado esperado

Duas requisições simultâneas com `expected_queue_id=10` não podem criar duas filas:

1. a primeira bloqueia e substitui a fila 10 pela fila 11;
2. a segunda encontra a fila 11;
3. a segunda retorna `409 queue_changed`;
4. somente a fila 11 permanece ativa.

## 8. Endpoint de listagem

### 8.1 Contrato

```http
GET /api/activity-queue/activities/?group_id=3
Authorization: Api-Key ...
```

A rota é somente leitura: não apresenta o próximo item, não cria fila e não altera
posições.

Ela localiza a fila ativa do mesmo `scope_key` e grupo e retorna até 30 itens operacionais:

```text
presented + started + pending
```

ordenados por `position`.

Itens `completed`, `skipped` e `expired` não ocupam as 30 posições da prévia.

### 8.2 Resposta

Quando existe fila:

```json
{
  "queue": {
    "id": 11,
    "group_id": 3,
    "group_name": "Jogos",
    "mode": "normal",
    "pool_number": 4,
    "pool_size": 48,
    "skip_locked": false
  },
  "returned_count": 30,
  "available_count": 48,
  "has_more": true,
  "statistics_scope": "all_time",
  "activities": [
    {
      "queue_item_id": 201,
      "position": 1,
      "state": "presented",
      "activity_id": 8,
      "name": "Path of Exile 2",
      "category": {
        "id": 21,
        "name": "Jogos"
      },
      "execution_count": 12,
      "last_execution_at": "2026-07-26T22:10:00Z",
      "skip_count": 3
    }
  ]
}
```

Quando não existe fila ativa, retornar `200 OK`:

```json
{
  "queue": null,
  "returned_count": 0,
  "available_count": 0,
  "has_more": false,
  "statistics_scope": "all_time",
  "activities": []
}
```

Uma fila com menos de 30 itens retorna somente os itens existentes. O limite é fixo no
backend nesta versão e não deve aceitar aumento por query parameter.

### 8.3 Definição dos indicadores

Os indicadores são globais e históricos por atividade:

```text
execution_count =
    COUNT(History)
    WHERE History.end_time IS NOT NULL
      AND History.schedule.state = completed

last_execution_at =
    MAX(History.end_time)
    aplicando o mesmo filtro de execução concluída

skip_count =
    COUNT(ActivityPreferenceEvent)
    WHERE event_type = skipped
```

Regras:

- uma execução apenas iniciada não conta como executada;
- uma execução concluída conta uma vez;
- um pulo conta uma vez mesmo que a atividade entre em uma fila posterior;
- concluir uma revisão gera `skipped_completed`, mas não aumenta `skip_count`;
- `last_execution_at` é `null` quando nunca houve execução concluída;
- datas devem ser serializadas em ISO 8601/UTC;
- contadores não devem depender de `Activity.executions_today` ou
  `Activity.last_executed`.

As agregações de histórico e pulo não devem multiplicar linhas por `JOIN`. A implementação
deve usar `Count(..., distinct=True)`, subqueries independentes ou agregação em lote.

## 9. Serviço de domínio

Adicionar funções equivalentes em `apps/pomodoro/services/activity_queue.py`:

```python
recreate_active_queue(
    *,
    scope_key: str,
    selected_group: Group | None,
    expected_queue_id: int,
    rng=random,
) -> QueueRecreationResult

list_active_queue_activities(
    *,
    scope_key: str,
    selected_group: Group | None,
    limit: int = 30,
) -> QueueActivityListResult
```

Views devem permanecer finas e somente:

- validar entrada;
- construir `scope_key`;
- chamar o serviço;
- serializar o resultado;
- mapear conflitos para HTTP.

O limite interno deve ser constante e não controlado livremente pelo cliente.

## 10. Concorrência e transações

`recreate_active_queue()` deve usar `transaction.atomic()` e bloquear:

- o grupo selecionado, quando necessário para limites;
- a fila ativa por `(scope_key, group)`;
- seus itens operacionais;
- a execução aberta do escopo;
- a criação da nova fila protegida pela constraint
  `unique_active_queue_per_scope_group`.

Ordem recomendada:

```text
normalizar e validar grupo
→ bloquear fila ativa
→ comparar expected_queue_id
→ bloquear/verificar execução aberta
→ rejeitar skipped_review
→ capturar pulos e eventos existentes
→ calcular e validar candidatos
→ expirar itens não consumidos
→ cancelar fila anterior
→ criar fila normal com recreated_from
→ criar itens e posições
→ confirmar contadores
```

Nenhum estado intermediário deve ficar persistido se qualquer etapa falhar.

## 11. Impacto nos contratos existentes

Continuam inalterados:

```http
GET  /api/activities/next/
POST /api/activity-queue/items/{queue_item_id}/skip/
POST /api/activities/{activity_id}/start/
POST /api/activities/complete/
```

A recriação manual substitui a regra da `SPEC-BACK-007` somente neste ponto:

- uma fila normal cancelada explicitamente por recriação não gera revisão automática;
- seus pulos elegíveis são carregados como candidatos da nova fila normal;
- a revisão automática continua obrigatória quando a fila normal termina pelo consumo
  natural.

A prioridade e o isolamento por grupo das `SPEC-BACK-009` e `SPEC-BACK-010` permanecem
vigentes.

## 12. Arquivos previstos

| Arquivo | Alteração |
| --- | --- |
| `apps/pomodoro/models.py` | Adicionar `ActivityQueue.recreated_from`. |
| `apps/pomodoro/migrations/0016_*.py` | Migration aditiva do vínculo. |
| `apps/pomodoro/services/activity_queue.py` | Recriação, expiração controlada e listagem. |
| `apps/pomodoro/serializers.py` | Serializers dos novos contratos. |
| `apps/pomodoro/views.py` | ViewSet fino para recriar e listar. |
| `apps/pomodoro/urls.py` | Registrar as duas rotas. |
| `apps/pomodoro/test_spec_back_011.py` | Cobertura funcional, API e concorrência. |
| `README.md` | Documentar os contratos depois da implementação. |
| `docs/postman/*` | Atualizar coleção e exemplos depois da implementação. |

O número da migration deve ser confirmado contra o estado da branch no início da
implementação.

## 13. Testes obrigatórios

### 13.1 Recriação básica

- recriar uma fila normal cancela a anterior;
- a nova fila possui `recreated_from`;
- somente uma fila permanece ativa;
- `pool_number` é incrementado;
- a ordem nova é persistida;
- a mesma fonte pseudoaleatória produz teste determinístico;
- falha ao criar a nova fila reverte o cancelamento.

### 13.2 Atividades puladas

- pulo anterior e evento `skipped` permanecem inalterados;
- atividade pulada elegível aparece uma vez na fila nova;
- dois pulos da mesma atividade em filas diferentes contam duas vezes;
- recriar não cria evento duplicado;
- atividade pulada inelegível é omitida e reportada em `skipped_not_requeued`;
- pular a nova ocorrência cria um novo evento;
- pulos de outro grupo ou de outro escopo não são carregados.

### 13.3 Proteções

- `expected_queue_id` ausente retorna `400`;
- ID desatualizado retorna `queue_changed`;
- clique duplo cria somente uma fila;
- execução aberta bloqueia recriação;
- `skipped_review` bloqueia recriação;
- grupo inexistente retorna `404`;
- fila inexistente retorna `404`;
- nenhuma atividade elegível mantém a fila anterior ativa.

### 13.4 Randomização e isolamento

- premium vigente permanece antes das atividades normais;
- atividades normais continuam randomizadas;
- fila específica contém somente atividades do grupo;
- `Todos` continua agregador;
- mais de 30 atividades continuam armazenadas na fila;
- nenhuma atividade aparece duas vezes na mesma fila.

### 13.5 Listagem

- retorna no máximo 30 itens;
- retorna os 30 primeiros itens operacionais por posição;
- não apresenta nem avança item;
- não cria fila quando não existe uma ativa;
- informa `has_more` e `available_count`;
- ignora itens concluídos, pulados e expirados na prévia;
- inclui nome e categoria;
- conta somente execuções concluídas;
- calcula a última conclusão pelo `History`;
- conta somente eventos `skipped`;
- `skipped_completed` não aumenta `skip_count`;
- atividade sem execução retorna data nula;
- agregações não sofrem multiplicação por joins.

### 13.6 Banco

- suíte completa em SQLite isolado;
- migrations do zero em SQLite;
- migration e testes críticos em PostgreSQL;
- teste concorrente de recriação em PostgreSQL, porque `select_for_update` não é
  representativo no SQLite.

## 14. Validações de implementação

```bash
poetry run python manage.py makemigrations --check
poetry run python manage.py check
poetry run python manage.py test
```

No ambiente de PostgreSQL isolado:

```bash
poetry run python manage.py migrate --noinput
poetry run python manage.py test apps.pomodoro.test_spec_back_011
```

Testes automatizados devem continuar usando `config.settings.test` e o SQLite isolado
configurado pelo projeto. Nunca usar o banco de desenvolvimento ou produção na suíte.

## 15. Critérios de aceite

- [ ] O botão futuro possui uma operação backend distinta de recarregar.
- [ ] A recriação afeta somente o escopo e grupo solicitados.
- [ ] A fila anterior permanece auditável e é marcada como cancelada.
- [ ] Pulos e eventos anteriores não são apagados nem duplicados.
- [ ] Atividades puladas ainda elegíveis entram exatamente uma vez na nova fila.
- [ ] A nova ordem continua aleatória, ponderada e compatível com premium.
- [ ] Revisões obrigatórias não podem ser descartadas por recriação.
- [ ] Execuções abertas não são interrompidas.
- [ ] A fila total continua podendo conter mais de 30 atividades.
- [ ] A rota de leitura retorna no máximo 30 itens operacionais.
- [ ] Os indicadores usam históricos persistidos e definições inequívocas.
- [ ] Concorrência não cria duas filas ativas.
- [ ] Testes e validação PostgreSQL estão registrados.

## 16. Riscos e mitigação

| Risco | Mitigação |
| --- | --- |
| Clique duplo recriar duas filas | `expected_queue_id`, lock e constraint da fila ativa. |
| Perda de histórico ao substituir | Cancelamento lógico; nenhum `DELETE`; `PROTECT` na linhagem. |
| Recriação burlar revisão obrigatória | Rejeitar `mode=skipped_review`. |
| Cancelar execução em andamento | Rejeitar qualquer execução aberta no escopo. |
| Retornar estatísticas multiplicadas | Agregações independentes ou contagens distintas. |
| Confundir prévia de 30 com tamanho da fila | Expor `pool_size`, `available_count` e `has_more`. |
| Campo legado de última execução estar obsoleto | Derivar `last_execution_at` de `History.end_time`. |
| Divergência entre SQLite e PostgreSQL | Teste concorrente e migration também em PostgreSQL. |

## 17. Dependência futura do frontend

Depois da implementação do backend, o frontend deverá:

- diferenciar “Recarregar fila” de “Recriar nova lista”;
- obter e enviar `expected_queue_id`;
- pedir confirmação antes da recriação;
- desabilitar a ação durante execução e em `skipped_review`;
- limpar caches locais da fila anterior após `201`;
- carregar a prévia pela nova rota;
- tratar `queue_changed` recarregando o estado canônico;
- nunca simular randomização local.

Essa etapa não faz parte desta spec de backend.

## 18. Histórico

| Data | Versão | Alteração |
| --- | --- | --- |
| 2026-07-27 | 1.0 | Planejamento inicial após análise do backend e do contrato consumido pelo frontend. |
