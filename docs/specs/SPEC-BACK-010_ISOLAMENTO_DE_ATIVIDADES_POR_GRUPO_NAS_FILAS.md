# SPEC-BACK-010 — Isolamento de atividades por grupo nas filas

## 1. Status

Proposta para ajuste.

Esta spec corrige a regra de roteamento global de atividades premium definida na
`SPEC-BACK-009`. A prioridade premium continua válida, mas passa a atuar somente dentro
das filas em que a atividade é elegível pelo grupo.

## 2. Contexto

O backend persiste uma fila ativa por combinação de escopo e grupo:

```text
(scope_key, group_id)
```

Essa unicidade é protegida pela constraint `unique_active_queue_per_scope_group`.
Atividades normais também respeitam o isolamento esperado:

- uma fila de grupo específico recebe atividades cuja categoria pertence ao mesmo grupo;
- a fila do grupo padrão `Todos` funciona como visão agregadora e pode receber atividades
  de qualquer grupo;
- a criação ou reativação de uma atividade normal reconcilia somente sua fila específica e
  a fila agregadora `Todos`.

Entretanto, a implementação da prioridade premium introduz uma exceção de roteamento:

- `eligible_activities()` inclui premium vigente de qualquer grupo ao criar uma fila;
- `_eligible_premiums()` consulta premium vigente globalmente para cada fila ativa;
- `activity_is_eligible(..., allow_global_premium=True)` aceita uma atividade premium em
  fila de outro grupo;
- `reconcile_activity()` e `reconcile_premium_queue()` inserem ou preservam esse item
  estrangeiro;
- a leitura da fila mantém o item enquanto o premium estiver vigente;
- o job `reconcile_premium_queues` replica premium em todas as filas normais ativas.

Como consequência, as filas são independentes como registros, mas não são isoladas em sua
composição: uma atividade premium da categoria do grupo A pode ser adicionada e executada
pela fila específica do grupo B.

## 3. Objetivo

Garantir que a composição de cada fila específica seja isolada pelo grupo da categoria da
atividade, independentemente de a atividade ser normal ou premium.

Premium deve definir prioridade de ordenação, não permissão de roteamento entre grupos.

## 4. Invariantes funcionais

### 4.1 Fila de grupo específico

Para toda fila cujo grupo não seja o grupo padrão `Todos`, qualquer item elegível nos
estados `pending` ou `presented` deve obedecer:

```text
queue_item.activity.category.group_id == queue.group_id
```

Uma atividade de outro grupo não pode ser:

- incluída na geração inicial da fila;
- inserida por criação, reativação ou alteração;
- inserida ou promovida pelo job de premium;
- preservada como elegível durante a leitura;
- oferecida como próxima atividade.

### 4.2 Fila agregadora `Todos`

O grupo marcado com `is_default=True` mantém a semântica agregadora existente. Sua fila
pode conter atividades de qualquer grupo.

`Todos` continua sendo uma fila persistente própria, com ordem, item apresentado, pulos,
revisão e histórico independentes das filas específicas.

Essa exceção deve depender de `Group.is_default`, não do nome textual do grupo.

### 4.3 Prioridade premium

Dentro de cada fila elegível, premium vigente continua à frente das atividades normais na
região pendente.

O conjunto premium elegível de uma fila deve ser:

```text
fila específica = premium vigente do mesmo grupo
fila Todos       = premium vigente de qualquer grupo
```

As demais regras da `SPEC-BACK-009` permanecem válidas:

- preservar itens `presented` ou `started` quando ainda pertencem ao grupo da fila;
- reordenar somente a região pendente;
- embaralhar somente o lote de premium novos;
- preservar a ordem relativa do prefixo premium existente e dos itens normais;
- respeitar limites diários e impedir duplicidade de item e posição;
- não reordenar filas fechadas ou canceladas.

Ficam substituídas especificamente as decisões da `SPEC-BACK-009` que determinam inclusão
premium global em filas de grupos diferentes.

### 4.4 Criação, alteração e reativação de atividade

Ao criar, alterar ou reativar uma atividade:

1. identificar o grupo atual pela categoria;
2. inserir a atividade na fila normal ativa desse grupo, quando elegível;
3. inserir a atividade na fila normal ativa de `Todos`, quando elegível;
4. não inserir a atividade em nenhuma fila específica de outro grupo;
5. quando houver mudança de categoria entre grupos, expirar itens não iniciados nas filas
   que deixaram de ser elegíveis e inserir nas filas que passaram a ser elegíveis;
6. aplicar a prioridade premium apenas depois de determinar as filas elegíveis.

O comportamento deve ser idêntico para API, Admin, importações e qualquer fluxo que use
`reconcile_activity()`.

### 4.5 Saneamento de itens estrangeiros existentes

A implantação deve sanear filas ativas criadas sob a regra premium global.

Para uma fila específica, um item cuja atividade pertença a outro grupo deve ser tratado
assim:

- `pending`: alterar para `expired`;
- `presented`, sem execução aberta: alterar para `expired`;
- `started`, ou associado a execução `preparing`/`running`: preservar até a finalização,
  sem interromper a execução;
- `completed`, `skipped` ou `expired`: preservar como histórico, sem reativar;
- item de fila fechada ou cancelada: preservar sem alteração.

A invariante de isolamento operacional se aplica aos itens que ainda podem ser
apresentados ou iniciados. Registros históricos estrangeiros podem permanecer para
auditoria.

Após o saneamento, `pool_size` e `consumed_count` devem continuar coerentes com o contrato
atual. Nenhum item deve ser apagado fisicamente.

### 4.6 Filas de revisão

Novas filas `skipped_review` não podem receber atividade de outro grupo específico. A fila
de revisão de `Todos` continua agregadora.

Se já existir fila de revisão ativa com item estrangeiro legado:

- expirar item `pending` ou `presented`;
- preservar execução iniciada;
- preservar estados finais para auditoria.

O saneamento de composição não autoriza reembaralhar os itens válidos da revisão.

## 5. Requisitos técnicos

### 5.1 Centralização da elegibilidade

Eliminar a possibilidade de uma chamada habilitar premium global por parâmetro.

`activity_is_eligible()` deve representar uma única regra de pertencimento:

```python
def activity_belongs_to_queue(activity, queue_group):
    return (
        queue_group.is_default
        or activity.category.group_id == queue_group.id
    )
```

Premium deve ser avaliado somente depois desse predicado.

Remover ou tornar inoperante o contrato `allow_global_premium=True`, evitando que um novo
chamador reintroduza o vazamento entre grupos.

### 5.2 Geração inicial

`eligible_activities(selected_group=group)` deve:

- filtrar estritamente por `category__group=group` quando `group.is_default=False`;
- não usar `OR premium` para ampliar o conjunto;
- manter o conjunto global somente para `group.is_default=True`;
- continuar ordenando os premium elegíveis antes dos normais.

### 5.3 Reconciliação de uma atividade

`reconcile_activity()` pode continuar bloqueando e percorrendo as filas normais ativas,
pois precisa:

- expirar o item de uma fila antiga após troca de grupo;
- inserir nas filas atualmente elegíveis;
- reconciliar a fila agregadora.

Entretanto, a decisão de inserir ou preservar deve usar a elegibilidade isolada por grupo.
Uma atividade premium não pode contornar esse filtro.

### 5.4 Reconciliação premium

`_eligible_premiums(queue)` deve consultar:

- apenas premium do grupo da fila, para grupo específico;
- todos os premium, para a fila `Todos`.

`reconcile_premium_queue()` deve também expirar da região não iniciada qualquer premium
estrangeiro legado antes de ordenar o conjunto válido.

O comando `reconcile_premium_queues` permanece disponível e idempotente. Ele passa a
executar duas responsabilidades por fila ativa:

1. sanear itens estrangeiros não iniciados;
2. ordenar e inserir somente os premium elegíveis para o grupo da fila.

O resumo do comando deve distinguir, no mínimo:

```text
queues_checked
items_inserted
items_promoted
items_demoted
foreign_items_expired
errors
failed_queue_ids
```

### 5.5 Proteção na leitura

`get_or_create_active_queue()` deve sanear a fila bloqueada antes de selecionar o próximo
item. Essa proteção impede que uma fila ainda não processada pelo job apresente um premium
estrangeiro legado.

Depois do saneamento, o serviço deve finalizar ou avançar a fila normalmente caso não
reste item elegível.

### 5.6 Concorrência

API/Admin, job e leitura concorrentes devem bloquear recursos em ordem estável:

```text
fila -> itens da fila
```

O ajuste não pode:

- criar posições duplicadas;
- duplicar uma atividade na mesma fila;
- expirar uma execução já iniciada;
- ressuscitar item histórico;
- inserir item em fila fechada ou cancelada;
- deixar um item estrangeiro `pending` ou `presented` depois da reconciliação confirmada.

### 5.7 Persistência

Não é necessária migration de schema.

O saneamento deve ocorrer por serviço idempotente e pelo comando operacional, porque o
estado das filas é mutável e pode mudar entre a aplicação de uma migration e a ativação da
nova versão.

## 6. Contrato HTTP

Não há novo endpoint nem remoção de campos.

`GET /api/activities/next/?group_id={id}` deve retornar:

- atividade do mesmo grupo solicitado; ou
- `404 no_activity_available` quando não houver atividade elegível.

Ele nunca deve retornar atividade de outro grupo apenas por ela ser premium.

Sem `group_id`, o contrato atual do grupo padrão `Todos` permanece agregador.

Os campos `queue_group_id` e `activity.group_id` tornam a invariante observável:

```text
grupo específico: queue_group_id == activity.group_id
Todos:            queue_group_id pode ser diferente de activity.group_id
```

## 7. Critérios de aceite

- Existe no máximo uma fila ativa por `(scope_key, group)`.
- Uma fila específica nova contém somente atividades do próprio grupo.
- Uma atividade premium de A não é inserida nem apresentada na fila específica de B.
- A fila de A prioriza premium de A antes das atividades normais de A.
- A fila de B permanece inalterada quando uma atividade de A é criada ou promovida.
- A fila `Todos` continua recebendo e priorizando atividades de A e B.
- Criar atividade normal ou premium reconcilia apenas o grupo de origem e `Todos`.
- Mudar a categoria de A para B expira itens não iniciados em A e insere em B e `Todos`.
- Desativar uma atividade expira seus itens não iniciados em todas as filas afetadas.
- O job não replica premium entre grupos.
- O job expira item estrangeiro legado `pending` ou `presented`.
- A leitura não apresenta item estrangeiro legado antes da execução do job.
- Execução estrangeira já iniciada é preservada até terminar.
- Filas de revisão específicas não recebem nem apresentam atividade estrangeira.
- Filas fechadas e canceladas permanecem históricas e imutáveis.
- Repetir a reconciliação não altera novamente posições ou estados.
- Constraints de atividade e posição continuam válidas no PostgreSQL.
- Contratos HTTP existentes permanecem compatíveis.

## 8. Testes obrigatórios

### 8.1 Geração e leitura

- criar filas para A, B e `Todos`;
- confirmar que cada fila específica contém somente seu grupo;
- confirmar que `Todos` contém atividades de A e B;
- criar premium de A antes da fila de B e confirmar que ele não entra em B;
- criar premium de A depois da fila de B e confirmar que ele não é reconciliado em B;
- confirmar prioridade premium dentro de A e dentro de `Todos`;
- confirmar `404` em B quando somente A possui atividades.

### 8.2 Reconciliação cadastral

- criar atividade normal de A e inserir uma vez em A e `Todos`;
- criar atividade premium de A e inserir uma vez em A e `Todos`, nunca em B;
- promover atividade existente de A para premium e não alterar a composição de B;
- reativar atividade premium e manter o mesmo isolamento;
- trocar categoria de A para B e validar expiração/inserção;
- executar a reconciliação duas vezes sem duplicar itens nem posições;
- validar os mesmos casos pela API e pelo Admin.

### 8.3 Saneamento legado

- expirar premium estrangeiro `pending`;
- expirar premium estrangeiro `presented` sem execução aberta;
- preservar item estrangeiro `started`;
- preservar item associado a execução `preparing` ou `running`;
- não alterar estados finais;
- sanear fila de revisão ativa sem reordenar itens válidos;
- não alterar fila fechada ou cancelada;
- confirmar contadores e resumo `foreign_items_expired`.

### 8.4 Job e concorrência

- `--dry-run` contabiliza o saneamento sem persistir;
- execução real persiste o saneamento;
- segunda execução é idempotente;
- falha em uma fila não impede o processamento das demais;
- PATCH premium, job e GET concorrentes não causam vazamento, duplicidade ou perda de
  posição.

### 8.5 Regressão

- skip, start, complete e revisão continuam independentes por fila;
- favoritos continuam ponderados por `scope_key` e grupo;
- limites diários continuam calculados para o grupo da fila;
- o grupo padrão continua sendo identificado por `is_default`;
- criação de fila e reconciliação continuam seguras no PostgreSQL.

## 9. Plano de implementação

1. criar um predicado central de pertencimento da atividade à fila;
2. remover o desvio `allow_global_premium`;
3. restringir `eligible_activities()` para geração inicial por grupo;
4. restringir `_eligible_premiums()` ao grupo da fila, preservando `Todos`;
5. adaptar `reconcile_activity()` para inserir somente no grupo de origem e em `Todos`;
6. implementar saneamento idempotente de itens estrangeiros não iniciados;
7. integrar o saneamento ao job e à leitura da fila;
8. adicionar `foreign_items_expired` ao resultado e ao comando;
9. substituir os testes da `SPEC-BACK-009` que exigem premium global;
10. adicionar testes de serviço, API, Admin, comando, concorrência e PostgreSQL;
11. documentar no deploy a execução inicial de:

```bash
python manage.py reconcile_premium_queues
```

## 10. Fora de escopo

- remover ou alterar a semântica agregadora de `Todos`;
- criar uma fila compartilhada entre usuários ou dispositivos além do `scope_key` atual;
- apagar fisicamente itens históricos estrangeiros;
- interromper execução já iniciada;
- alterar pesos de favoritos;
- mudar limites diários de categoria ou grupo;
- introduzir Celery ou novo broker;
- alterar o contrato público de premium.

## 11. Riscos e mitigação

- **Fila ativa já contaminada:** sanear no comando de deploy e novamente na leitura.
- **Execução estrangeira em andamento:** preservar até a conclusão e impedir novas
  apresentações estrangeiras.
- **Regressão em `Todos`:** cobrir explicitamente a exceção agregadora em geração,
  reconciliação e API.
- **Reintrodução futura de premium global:** centralizar pertencimento e remover o parâmetro
  que contorna o grupo.
- **Colisão de posições durante reconciliação:** manter posições temporárias e transação
  com lock da fila e dos itens.
- **Custo de percorrer todas as filas:** filtrar filas candidatas por grupo quando possível
  e manter o job como mecanismo de consistência em lote.

## 12. Evidências da análise

- `ActivityQueue` já possui unicidade ativa por `scope_key` e `group`.
- `_create_normal_queue()` cria a fila com o grupo selecionado.
- `eligible_activities()` amplia filas específicas com `OR premium vigente`.
- `_eligible_premiums()` consulta premium global sem filtro de grupo.
- `reconcile_activity()` avalia todas as filas com `allow_global_premium=True`.
- os testes da `SPEC-BACK-009` exigem hoje premium de outro grupo na fila local.
- a `SPEC-BACK-009` declara premium como exceção explícita ao roteamento normal.

Portanto, o isolamento estrutural das filas existe, mas a composição não respeita
isolamento por grupo enquanto a exceção premium global permanecer.
