# SPEC-BACK-009 — Priorização premium em filas ativas

## 1. Status

Proposta para implementação no backend.

## 2. Contexto

O backend mantém filas persistentes por escopo e grupo. A ordem é calculada uma vez na
criação da fila e persistida em `ActivityQueueItem.position`.

A geração inicial já prioriza atividades com premium vigente. Em
`apps/pomodoro/services/activity_queue.py`, `_weighted_order()` inclui
`not activity.is_premium_active` como primeiro critério de ordenação. Assim, todas as
atividades premium vigentes ficam antes das atividades normais, com ponderação e
randomização dentro de cada conjunto.

Há cobertura automatizada para esse comportamento em
`ActivityNextViewSetTests.test_next_prioritizes_premium_activity`.

Entretanto, uma fila já criada não é reorganizada quando uma atividade existente passa a
ter premium vigente:

- `ActivityViewSet.perform_update()` e `ActivityAdmin.save_model()` chamam
  `reconcile_activity()` após a alteração;
- `activity_snapshot()` não registra `premium`, `premium_from`, `premium_until` nem o
  estado derivado `is_premium_active`;
- para um item já presente na fila, `reconcile_activity()` somente expira o item quando
  ele deixa de ser elegível e não altera sua posição;
- o endpoint de próxima atividade lê o item apresentado/iniciado ou o menor
  `position` pendente, sem recalcular prioridade premium.

Consequentemente, marcar como premium uma atividade que está no fim de uma fila ativa
não faz com que ela seja oferecida antes das atividades normais restantes.

## 3. Objetivo

Garantir que uma atividade que passe de não premium vigente para premium vigente seja
reposicionada nas filas normais ativas e elegíveis para ser executada antes das atividades
normais ainda pendentes.

O reposicionamento deve preservar o item atualmente apresentado ou iniciado, o histórico
da fila e a ordem relativa dos demais itens.

## 4. Decisões funcionais

### 4.1 Conceito de premium vigente

A priorização deve usar exclusivamente `Activity.is_premium_active`, considerando:

- `premium=True`;
- `premium_from <= data local atual`;
- `premium_until >= data local atual`.

Alterar apenas o booleano `premium` sem datas válidas não deve criar um estado premium
inconsistente. API e Admin devem manter a validação já definida no domínio: as duas datas
são obrigatórias quando `premium=True` e `premium_from` não pode ser posterior a
`premium_until`.

### 4.2 Transição que dispara promoção

A promoção ocorre quando o estado derivado muda de:

```text
is_premium_active: false -> true
```

Isso inclui:

- atividade normal marcada como premium com vigência iniciando hoje;
- correção de `premium_from` ou `premium_until` que torne o premium vigente;
- reativação do booleano premium dentro de um período válido.

Uma alteração que mantenha `is_premium_active=True` deve ser idempotente e não mudar
novamente a posição.

### 4.3 Significado de “ir para a frente”

A fila possui uma fronteira imutável e uma região pendente:

- itens `completed`, `skipped`, `expired`, `presented` ou `started` não devem ser
  deslocados;
- somente itens `pending` podem ser reordenados;
- um item `presented` continua sendo retornado até ser iniciado, pulado ou invalidado;
- um item `started` nunca pode ser preemptado.

Na região pendente, a ordem passa a ser:

```text
[premium vigente pendente] -> [atividade normal pendente]
```

A atividade recém-promovida deve ser colocada no final do bloco premium pendente e antes
do primeiro item normal pendente. Essa regra:

- garante prioridade sobre todas as atividades normais;
- preserva a ordem relativa dos premium que já estavam à frente;
- preserva a ordem relativa das atividades normais;
- produz resultado determinístico e idempotente.

Se não houver outro premium pendente, a atividade promovida assume a primeira posição
pendente. Se ela já estiver no bloco premium correto, nenhuma escrita é necessária.

### 4.4 Filas afetadas

A promoção deve ser aplicada a todas as filas que atendam simultaneamente a:

- `state=active`;
- `mode=normal`;
- a atividade já pertence à fila ou é elegível para inclusão;
- o grupo da fila é o grupo da atividade ou o grupo padrão `Todos`;
- o item está em estado `pending`, quando já existir.

Filas de grupos diferentes não devem ser alteradas.

Filas de revisão (`skipped_review`) não devem ser reordenadas. Elas representam a ordem
persistida de uma revisão originada por pulos e possuem semântica própria.

Filas fechadas ou canceladas são históricas e não devem ser modificadas.

### 4.5 Atividade que ainda não pertence à fila

Se a atividade for elegível, mas ainda não existir em uma fila normal ativa aplicável, a
reconciliação deve incluí-la diretamente no final do bloco premium pendente.

A inserção aleatória atual continua válida para atividades novas ou reativadas que não
sejam premium vigentes.

### 4.6 Item apresentado ou iniciado da própria atividade

Se a atividade promovida já estiver `presented` ou `started`, nenhuma posição deve ser
alterada. Ela já é o item corrente e continuará obedecendo ao contrato de idempotência da
apresentação e da execução.

Itens consumidos ou expirados não devem ser ressuscitados nem duplicados na mesma fila.

### 4.7 Fim da vigência

O fim do premium não deve reembaralhar retroativamente uma fila ativa. A posição
conquistada permanece como parte do snapshot da fila. O item passa a ser serializado com
`is_premium_active=False`, e novas filas não lhe dão prioridade.

Essa decisão evita movimentações inesperadas e escritas diárias em massa. Uma futura
regra de rebaixamento deve ser tratada em spec separada.

### 4.8 Vigência futura alcançada sem atualização cadastral

Uma atividade pode ser salva hoje com `premium_from` no futuro e tornar-se premium sem
novo PATCH. Para que a regra não dependa de scheduler, ao abrir ou reutilizar uma fila
normal o backend deve reconciliar, de forma idempotente, os itens pendentes cujo premium
se tornou vigente na data local atual.

Essa reconciliação preguiçosa deve ocorrer dentro da mesma transação que bloqueia a fila,
antes da seleção do próximo item. Não deve alterar um item já `presented` ou `started`.

## 5. Requisitos técnicos

### 5.1 Snapshot da atividade

`activity_snapshot()` deve incluir ao menos:

```python
{
    "premium": activity.premium,
    "premium_from": activity.premium_from,
    "premium_until": activity.premium_until,
    "is_premium_active": activity.is_premium_active,
}
```

O valor derivado deve ser calculado antes do `serializer.save()` ou do salvamento no
Admin, usando a data local da requisição.

### 5.2 Serviço de reposicionamento

A lógica deve permanecer em `apps/pomodoro/services/activity_queue_reconciliation.py`,
sem ser duplicada em view, serializer ou Admin.

Criar uma operação interna equivalente a:

```python
promote_pending_premium(queue, item) -> bool
```

Ela deve:

1. exigir transação ativa;
2. trabalhar com a fila bloqueada por `select_for_update()`;
3. bloquear ou consultar de forma consistente os itens pendentes;
4. calcular o bloco de premium vigente;
5. preservar a ordem relativa dos outros itens;
6. trocar somente posições da região pendente;
7. usar posições temporárias fora da faixa atual para não violar
   `unique_queue_item_position` no PostgreSQL;
8. retornar se houve alteração;
9. manter `pool_size` e `consumed_count` inalterados quando houver somente reordenação.

Não é necessária migration de schema para esta feature.

### 5.3 Reconciliação no PATCH e no Admin

`reconcile_activity(activity, previous=previous)` deve detectar a transição para premium
vigente e promover o item em cada fila aplicável.

O fluxo deve ser atômico por alteração de atividade. Se uma fila falhar, a alteração não
deve deixar posições parcialmente atualizadas.

### 5.4 Reconciliação na leitura da fila

`get_or_create_active_queue()` deve reconciliar promoções por início de vigência depois de
bloquear a fila existente e antes de `present_next_item()` escolher o menor `position`.

A consulta deve ficar restrita aos itens pendentes da fila atual. Não deve fazer varredura
global de todas as filas a cada requisição.

### 5.5 Concorrência

PATCH simultâneo com `GET /api/activities/next/` não pode:

- gerar posições duplicadas;
- perder itens;
- apresentar uma atividade normal depois que a promoção premium já foi confirmada, salvo
  se essa atividade normal já estava `presented` antes do PATCH;
- alterar o item iniciado;
- criar a mesma atividade duas vezes na fila.

A constraint existente de posição e a unicidade entre fila e atividade continuam sendo a
última linha de defesa.

## 6. Contrato HTTP

Não há novo endpoint nem mudança de payload.

Fluxo esperado:

```http
PATCH /api/activities/{activity_id}/
Content-Type: application/json

{
  "premium": true,
  "premium_from": "2026-07-15",
  "premium_until": "2026-07-20"
}
```

Após resposta `200 OK`, a reorganização das filas ativas aplicáveis já deve estar
confirmada. O próximo `GET /api/activities/next/` retorna:

- o item já apresentado, se houver; ou
- o primeiro premium vigente pendente, incluindo a atividade recém-promovida conforme a
  ordem do bloco premium.

Datas ausentes ou intervalo inválido devem retornar `400 Bad Request` sem alterar a
atividade nem a fila.

## 7. Critérios de aceite

- Uma fila nova continua colocando todos os premium vigentes antes dos normais.
- Ao tornar premium vigente um item pendente de uma fila ativa, ele passa para antes de
  todos os normais pendentes.
- O item já apresentado ou iniciado não é preemptado.
- Premium já vigente não muda de posição em PATCH idempotente.
- A ordem relativa dos demais premium e dos normais é preservada.
- A promoção ocorre de forma independente na fila do grupo específico e na fila `Todos`.
- Filas de outros grupos, revisão, fechadas e canceladas não são alteradas.
- Uma atividade premium elegível ausente é inserida sem duplicidade no bloco premium.
- Uma vigência futura que começa hoje é promovida no próximo acesso à fila.
- O fim da vigência não rebaixa o item na fila existente.
- Não há colisão de `position` no PostgreSQL.
- Nenhum contrato HTTP existente é removido ou renomeado.

## 8. Testes obrigatórios

### 8.1 Serviço

- promover o único premium para a primeira posição pendente;
- inserir a promoção depois dos premium existentes e antes dos normais;
- preservar a ordem relativa dos demais itens;
- não mover item `presented`;
- não mover item `started`;
- não alterar estados finais;
- não alterar fila de revisão ou encerrada;
- atualizar filas do grupo específico e `Todos`;
- ignorar filas de outros grupos;
- repetir a reconciliação sem nova escrita nem mudança de posição;
- iniciar vigência pela passagem da data e reconciliar na leitura;
- executar com posições consecutivas sob a constraint única do PostgreSQL.

### 8.2 API

- criar fila com atividades normais, fazer PATCH tornando a última premium e confirmar a
  próxima atividade premium;
- manter a atividade já apresentada e entregar a premium logo após seu consumo;
- rejeitar premium sem datas;
- rejeitar `premium_from > premium_until`;
- confirmar que a resposta preserva `premium` e `is_premium_active`.

### 8.3 Concorrência e regressão

- PATCH e GET concorrentes não produzem duplicidade ou perda de posição;
- geração inicial continua priorizando premium;
- inserção aleatória de atividade normal continua funcionando;
- skip, start, complete e revisão de puladas mantêm o comportamento atual.

## 9. Plano de implementação

1. adicionar os campos premium ao snapshot;
2. centralizar a validação do intervalo premium no serializer/domínio;
3. implementar o reposicionamento transacional da região pendente;
4. integrar a promoção em `reconcile_activity()`;
5. integrar a ativação por passagem da data na leitura da fila;
6. adicionar testes de serviço, API, PostgreSQL e concorrência;
7. atualizar a documentação operacional somente se surgir novo comando ou configuração.

## 10. Fora de escopo

- alterar o peso histórico de favoritos;
- reordenar filas de revisão;
- rebaixar itens quando o premium expira;
- criar scheduler ou worker para vigências;
- mudar o contrato público da fila;
- reordenar itens já consumidos, apresentados ou iniciados;
- modificar filas históricas.

## 11. Riscos e mitigação

- **Colisão temporária de posições:** usar faixa temporária dentro da transação antes de
  atribuir as posições finais.
- **Preempção surpreendente:** manter `presented` e `started` imutáveis.
- **Deadlock entre PATCH e GET:** bloquear sempre fila antes dos itens e processar filas em
  ordem estável de `id`.
- **Custo na leitura:** restringir a reconciliação preguiçosa à fila ativa atual e aos itens
  pendentes.
- **Divergência entre API e Admin:** ambos devem continuar chamando o mesmo serviço após a
  mesma validação de domínio.

## 12. Definição de pronto

A feature está pronta quando todos os critérios de aceite estiverem cobertos por testes,
a suíte do app passar com banco de teste isolado e a movimentação de posições for validada
em PostgreSQL, além do SQLite usado nos testes locais.
