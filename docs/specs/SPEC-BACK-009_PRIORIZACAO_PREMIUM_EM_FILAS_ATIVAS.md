# SPEC-BACK-009 — Priorização premium em filas ativas

## 1. Status

Proposta revisada conforme decisão funcional: prioridade premium global com reconciliação
periódica.

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

Garantir que toda atividade premium vigente seja posicionada na frente de todas as filas
normais ativas, independentemente do grupo ou da categoria aos quais pertence, antes das
atividades normais ainda pendentes.

O reposicionamento deve preservar o item atualmente apresentado ou iniciado, o histórico
da fila, a ordem relativa dos premium já priorizados e a ordem relativa das atividades
normais.

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

### 4.2 Verificação periódica e promoção

A fonte de verdade deve ser um job idempotente executado, por padrão, a cada 15 minutos.
Em cada ciclo, ele deve descobrir o estado atual do banco; não deve depender de evento em
memória nem de uma execução anterior bem-sucedida.

A promoção ocorre quando o job encontra uma atividade com:

```text
is_premium_active = true
```

que ainda não pertence ao prefixo premium de uma fila ativa. Isso inclui:

- atividade normal marcada como premium com vigência iniciando hoje;
- correção de `premium_from` ou `premium_until` que torne o premium vigente;
- reativação do booleano premium dentro de um período válido;
- atividade com `premium_from` alcançado pela passagem do tempo;
- atividade premium vigente criada depois da fila.

O PATCH e o Admin podem chamar a mesma reconciliação imediatamente para reduzir a janela
de espera, mas o job periódico continua obrigatório como mecanismo de consistência. A
ausência ou falha do gatilho imediato não pode impedir a correção no próximo ciclo.

### 4.3 Significado de “ir para a frente”

A fila possui uma fronteira imutável e uma região pendente:

- itens `completed`, `skipped`, `expired`, `presented` ou `started` não devem ser
  deslocados;
- somente itens `pending` podem ser reordenados;
- um item `presented` continua sendo retornado até ser iniciado, pulado ou invalidado;
- um item `started` nunca pode ser preemptado.

Na região pendente, a ordem após cada ciclo deve ser:

```text
[premium novo embaralhado] -> [premium já priorizado] -> [atividade normal]
```

Para cada fila, o job deve:

1. identificar o prefixo de premium vigentes que já estava corretamente priorizado no
   início do ciclo;
2. localizar premium vigentes novos, ausentes ou posicionados fora desse prefixo;
3. embaralhar aleatoriamente somente esse lote novo;
4. inserir o lote novo antes do prefixo premium existente;
5. manter a ordem relativa do prefixo premium existente;
6. manter a ordem relativa das atividades normais.

Essa regra:

- garante prioridade sobre todas as atividades normais;
- evita reembaralhar os premium que já estavam à frente a cada 15 minutos;
- dá ordem aleatória justa ao lote de novos premium;
- preserva a ordem relativa das atividades normais;
- torna novas execuções do job idempotentes quando não houver mudança cadastral ou de
  vigência.

Se não houver premium novo, nenhuma posição deve ser escrita. O embaralhamento deve
aceitar uma fonte pseudoaleatória injetável para permitir testes determinísticos.

### 4.4 Filas afetadas

A promoção deve ser aplicada a todas as filas que atendam simultaneamente a:

- `state=active`;
- `mode=normal`;
- o item corrente da fila pode ser preservado;
- existe região pendente que possa receber itens.

Premium é uma exceção explícita ao roteamento normal da fila. Uma atividade premium
vigente deve ser incluída em todas as filas normais ativas, inclusive nas filas de grupos
diferentes do grupo de sua categoria e na fila `Todos`.

“Independentemente de grupo e categoria” altera a composição da fila, mas não desativa
regras de segurança e capacidade. A atividade ainda deve:

- estar ativa e possuir categoria válida;
- respeitar o limite diário de execuções de sua categoria;
- caber no saldo diário de minutos do grupo da fila de destino;
- não estar concluída no dia quando o modo normal proibir repetição;
- não possuir execução incompatível aberta.

Assim, grupo e categoria não impedem a prioridade ou inclusão global, mas seus limites
operacionais continuam protegidos.

Filas de revisão (`skipped_review`) não devem ser reordenadas. Elas representam a ordem
persistida de uma revisão originada por pulos e possuem semântica própria.

Filas fechadas ou canceladas são históricas e não devem ser modificadas.

### 4.5 Atividade que ainda não pertence à fila

Se a atividade premium vigente ainda não existir em uma fila normal ativa, a reconciliação
deve incluí-la no lote de premium novos daquela fila, mesmo que a categoria pertença a
outro grupo.

A inserção aleatória atual continua válida para atividades novas ou reativadas que não
sejam premium vigentes.

### 4.6 Item apresentado ou iniciado da própria atividade

Se a atividade promovida já estiver `presented` ou `started`, nenhuma posição deve ser
alterada. Ela já é o item corrente e continuará obedecendo ao contrato de idempotência da
apresentação e da execução.

Itens consumidos ou expirados não devem ser ressuscitados nem duplicados na mesma fila.

### 4.7 Fim da vigência

Quando a vigência termina, a atividade deixa de fazer parte do prefixo premium no próximo
ciclo. Ela deve ser movida para depois de todos os premium vigentes, preservando a ordem
relativa que possuía diante das demais atividades normais sempre que possível.

Essa normalização é necessária para garantir a invariante de que nenhuma atividade normal
permaneça à frente de uma premium vigente.

### 4.8 Vigência futura alcançada sem atualização cadastral

Uma atividade pode ser salva hoje com `premium_from` no futuro e tornar-se premium sem
novo PATCH. O job periódico deve detectá-la pela propriedade `is_premium_active` e
promovê-la no próximo ciclo, com atraso operacional máximo esperado de 15 minutos.

O acesso à fila pode executar uma verificação leve e idempotente antes de selecionar o
próximo item, mas isso é uma otimização de consistência e não substitui o job.

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

Criar operações internas equivalentes a:

```python
reconcile_premium_queue(queue, *, rng) -> ReconciliationResult
reconcile_all_premium_queues(*, rng) -> ReconciliationSummary
```

Para cada fila, o serviço deve:

1. exigir transação ativa;
2. trabalhar com a fila bloqueada por `select_for_update()`;
3. bloquear ou consultar de forma consistente os itens pendentes;
4. consultar o conjunto global de premium vigentes e operacionalmente elegíveis;
5. classificar o prefixo premium existente e o lote premium novo;
6. embaralhar somente o lote novo;
7. inserir ausentes e reordenar somente a região pendente;
8. preservar a ordem relativa do prefixo premium existente e dos itens normais;
9. usar posições temporárias fora da faixa atual para não violar
   `unique_queue_item_position` no PostgreSQL;
10. atualizar `pool_size` apenas quando houver inserção;
11. manter `consumed_count` inalterado;
12. retornar contadores de filas verificadas, itens inseridos, promovidos, rebaixados e
    erros.

Não é necessária migration de schema para esta feature.

### 5.3 Reconciliação no PATCH e no Admin

`reconcile_activity(activity, previous=previous)` deve poder executar a mesma regra para
uma atividade alterada, em todas as filas normais ativas, como gatilho imediato opcional.
Ele não pode aplicar uma regra de ordenação diferente da usada pelo job.

O fluxo deve ser atômico por alteração de atividade. Se uma fila falhar, a alteração não
deve deixar posições parcialmente atualizadas.

### 5.4 Job agendável

Disponibilizar um comando Django equivalente a:

```bash
python manage.py reconcile_premium_queues
```

O comando deve ser seguro para execução por cron, systemd timer ou scheduler da
infraestrutura a cada 15 minutos. Não é necessário introduzir Celery apenas para esta
feature.

Requisitos:

- processar filas em ordem estável de `id`;
- usar uma transação por fila, evitando uma transação global longa;
- aceitar `--dry-run` para diagnóstico sem escrita;
- impedir ou tolerar sobreposição de execuções;
- continuar nas demais filas quando uma fila falhar, registrando o erro e retornando
  status não zero ao final;
- emitir resumo estruturado sem dados sensíveis;
- ser idempotente quando executado repetidamente sem mudança no domínio.

A infraestrutura deve configurar a periodicidade. O repositório deve fornecer exemplo de
cron ou timer e documentar que 15 minutos é o intervalo padrão e a defasagem máxima
esperada para vigências ativadas apenas pelo tempo.

### 5.5 Reconciliação na leitura da fila

`get_or_create_active_queue()` pode reconciliar a fila atual depois do lock e antes da
seleção do menor `position`. Essa proteção reduz inconsistência caso o scheduler esteja
atrasado, mas deve reutilizar o mesmo serviço e limitar a consulta à fila atual.

### 5.6 Concorrência

Job, PATCH e `GET /api/activities/next/` simultâneos não podem:

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

Quando o gatilho imediato estiver habilitado, após resposta `200 OK` a reorganização já
deve estar confirmada. Sem o gatilho imediato, o job deve garanti-la em até 15 minutos. O
próximo `GET /api/activities/next/`, depois da reconciliação, retorna:

- o item já apresentado, se houver; ou
- o primeiro item do lote premium novo embaralhado; ou
- o primeiro premium já priorizado quando não houver premium novo.

Datas ausentes ou intervalo inválido devem retornar `400 Bad Request` sem alterar a
atividade nem a fila.

## 7. Critérios de aceite

- Uma fila nova continua colocando todos os premium vigentes antes dos normais.
- Todo premium vigente elegível existe em todas as filas normais ativas, sem restrição de
  grupo ou categoria de origem.
- Os premium já priorizados permanecem na frente e conservam sua ordem relativa.
- Os premium descobertos no ciclo são embaralhados entre si e inseridos antes dos premium
  já priorizados.
- Ao tornar premium vigente um item pendente, ele passa para antes de todos os normais em
  no máximo 15 minutos, ou imediatamente quando o gatilho de escrita estiver habilitado.
- O item já apresentado ou iniciado não é preemptado.
- Nova execução do job sem mudanças não altera posições.
- A ordem relativa dos normais é preservada.
- Filas de revisão, fechadas e canceladas não são alteradas.
- Uma atividade premium ausente é inserida sem duplicidade em todas as filas normais.
- Uma vigência futura que começa hoje é promovida no próximo ciclo.
- O fim da vigência remove o item do prefixo premium.
- Limites diários de categoria e tempo do grupo de destino continuam respeitados.
- Não há colisão de `position` no PostgreSQL.
- Nenhum contrato HTTP existente é removido ou renomeado.

## 8. Testes obrigatórios

### 8.1 Serviço

- promover o único premium para a primeira posição pendente;
- preservar o prefixo premium existente;
- embaralhar somente o lote de premium novos com RNG controlado;
- inserir o lote novo antes do prefixo premium existente e dos normais;
- preservar a ordem relativa dos premium existentes e dos normais;
- não mover item `presented`;
- não mover item `started`;
- não alterar estados finais;
- não alterar fila de revisão ou encerrada;
- atualizar a fila do grupo específico, `Todos` e filas de outros grupos;
- respeitar limites de categoria e tempo do grupo de destino;
- inserir premium global ausente sem duplicidade;
- rebaixar premium expirado para depois do prefixo vigente;
- repetir o job sem nova escrita nem mudança de posição;
- iniciar vigência pela passagem da data e reconciliar no job;
- executar com posições consecutivas sob a constraint única do PostgreSQL.

### 8.2 API

- criar filas de grupos diferentes, tornar uma atividade premium e confirmar sua inclusão
  e prioridade global depois da reconciliação;
- manter a atividade já apresentada e entregar a premium logo após seu consumo;
- rejeitar premium sem datas;
- rejeitar `premium_from > premium_until`;
- confirmar que a resposta preserva `premium` e `is_premium_active`.

### 8.3 Comando agendável

- `--dry-run` informa alterações sem persistir posições;
- duas execuções consecutivas produzem alterações apenas na primeira;
- duas execuções sobrepostas não duplicam itens nem posições;
- falha em uma fila não impede a verificação das demais e gera saída não zero;
- resumo contém contadores de filas e itens processados;
- exemplo de agendamento executa a cada 15 minutos.

### 8.4 Concorrência e regressão

- job, PATCH e GET concorrentes não produzem duplicidade ou perda de posição;
- geração inicial continua priorizando premium;
- inserção aleatória de atividade normal continua funcionando;
- skip, start, complete e revisão de puladas mantêm o comportamento atual.

## 9. Plano de implementação

1. adicionar os campos premium ao snapshot;
2. centralizar a validação do intervalo premium no serializer/domínio;
3. implementar a consulta global de premium operacionalmente elegíveis;
4. implementar a classificação entre prefixo premium existente e lote novo;
5. implementar embaralhamento injetável e reposicionamento transacional;
6. integrar a mesma regra em `reconcile_activity()` como gatilho imediato opcional;
7. criar o comando `reconcile_premium_queues` com `--dry-run` e resumo;
8. fornecer exemplo de agendamento a cada 15 minutos;
9. integrar a proteção leve na leitura, se o custo medido for aceitável;
10. adicionar testes de serviço, comando, API, PostgreSQL e concorrência.

## 10. Fora de escopo

- alterar o peso histórico de favoritos;
- reordenar filas de revisão;
- ignorar limites diários de categoria ou de minutos do grupo de destino;
- introduzir Celery ou outro broker exclusivamente para esta rotina;
- mudar o contrato público da fila;
- reordenar itens já consumidos, apresentados ou iniciados;
- modificar filas históricas.

## 11. Riscos e mitigação

- **Colisão temporária de posições:** usar faixa temporária dentro da transação antes de
  atribuir as posições finais.
- **Preempção surpreendente:** manter `presented` e `started` imutáveis.
- **Deadlock entre PATCH e GET:** bloquear sempre fila antes dos itens e processar filas em
  ordem estável de `id`.
- **Aumento do tamanho das filas:** premium global pode ser replicado em todas as filas;
  consultar em lote e medir duração e quantidade de inserts por ciclo.
- **Custo periódico:** usar transação por fila, consultas em lote e não escrever filas que
  já respeitem a invariante.
- **Scheduler indisponível:** manter comando observável e permitir gatilho imediato e
  verificação leve na leitura como defesas adicionais.
- **Divergência entre API e Admin:** ambos devem continuar chamando o mesmo serviço após a
  mesma validação de domínio.

## 12. Definição de pronto

A feature está pronta quando todos os critérios de aceite estiverem cobertos por testes,
o comando estiver documentado e configurável para execução a cada 15 minutos, a suíte do
app passar com banco de teste isolado e a movimentação de posições for validada em
PostgreSQL, além do SQLite usado nos testes locais.
