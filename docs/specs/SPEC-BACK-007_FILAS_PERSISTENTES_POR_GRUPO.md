---

spec_id: SPEC-BACK-007
titulo: Filas persistentes independentes por grupo e ciclo obrigatório de atividades puladas
status: APPROVED
fase: AS_IS
situacao: IMPLEMENTADA
responsavel: Arquitetura de Software
criado_em: 2026-07-12
atualizado_em: 2026-07-12
documento_principal:

* SPEC-BACK-007
  dependencias:
* SPEC-BACK-003
* SPEC-BACK-004
* SPEC-BACK-005
* SPEC-BACK-006
  substitui: []
  substituida_por: []

---

# SPEC-BACK-007 — Filas persistentes independentes por grupo e ciclo obrigatório de atividades puladas

## 1. Objetivo

Evoluir o backend Pomodoro para implementar o fluxo completo de filas persistentes de atividades por grupo.

O sistema deve permitir que cada grupo possua uma fila própria, aleatória, persistida e independente, preservando sua posição mesmo quando o usuário alternar entre grupos ou dispositivos.

O fluxo deve:

* criar uma fila independente para cada grupo;
* criar uma fila independente para o grupo lógico `Todos`;
* incluir na fila normal todas as atividades elegíveis daquele grupo;
* manter a ordem sorteada persistida;
* permitir iniciar ou pular cada atividade durante o ciclo normal;
* registrar atividades iniciadas e concluídas;
* registrar atividades puladas;
* apresentar a próxima atividade após cada conclusão ou pulo;
* gerar imediatamente uma fila de revisão com as atividades puladas ao final do ciclo normal;
* impedir que atividades sejam puladas durante a fila de revisão;
* iniciar um novo ciclo normal somente depois da conclusão da revisão;
* reconciliar atividades criadas, ativadas ou alteradas com as filas normais ainda abertas;
* respeitar duração das atividades, limites das categorias e limites temporais configurados nos grupos;
* continuar permitindo somente uma execução ativa por escopo, mesmo que existam várias filas de grupos.

O objetivo funcional permanece sendo utilizar o acaso para distribuir as atividades realizadas no tempo livre, reduzindo a concentração prolongada em uma única atividade ou jogo.

---

## 2. Contexto

O backend já possui os seguintes componentes:

```text
Activity
Category
Group
ActivityQueue
ActivityQueueItem
ActivityPreferenceEvent
Schedule
History
```

Também já existem:

* fila persistida;
* item apresentado de maneira estável;
* início de atividade vinculado a um item da fila;
* execução persistente;
* contador baseado no backend;
* conclusão de atividade;
* ação de pular;
* eventos de preferência;
* modo `skipped_review`;
* bloqueio de pulo por meio de `skip_locked`;
* filtros por grupo;
* limite diário de execuções por categoria.

Entretanto, o comportamento atual possui as seguintes divergências:

1. existe apenas uma fila ativa por `scope_key`;
2. o grupo não participa da unicidade da fila ativa;
3. alternar de grupo pode reutilizar a fila de outro grupo;
4. a fila normal é limitada arbitrariamente a 30 atividades;
5. a revisão das puladas ocorre somente depois de cinco filas normais;
6. atividades puladas não são isoladas corretamente por grupo;
7. atividades novas ou alteradas não são reconciliadas com filas abertas;
8. o modelo `Group` não representa limite temporal;
9. não há cobertura automatizada para o ciclo completo por grupo.

O diagnóstico detalhado está registrado em:

```text
docs/handoff/02_handoff_analise_projeto.md
```

---

## 3. Escopo

Esta Specification contempla:

* fila ativa independente por grupo;
* fila ativa independente para `Todos`;
* normalização do grupo solicitado;
* unicidade de fila por `scope_key` e grupo;
* preservação de várias filas ativas simultâneas;
* preservação de somente uma execução ativa por `scope_key`;
* geração da lista completa de atividades elegíveis;
* randomização persistente;
* remoção do limite fixo de 30 itens;
* ciclo normal;
* registro de atividades puladas;
* ciclo obrigatório de revisão das puladas;
* bloqueio de pulo durante a revisão;
* isolamento de eventos e saldos de pulo por grupo;
* geração de um novo ciclo normal depois da revisão;
* reconciliação de atividades criadas ou alteradas;
* expiração de itens que se tornarem inelegíveis;
* inclusão aleatória de novos itens em filas normais abertas;
* limite diário de tempo por grupo;
* preservação do limite diário de execuções por categoria;
* preservação da duração individual da atividade;
* atualização dos serializers e endpoints afetados;
* migrations compatíveis com PostgreSQL;
* testes unitários e de integração;
* atualização da documentação;
* commit Git coeso ao final.

---

## 4. Fora de escopo

Não fazem parte desta Specification:

* alterar o frontend Flutter;
* alterar o frontend desktop;
* permitir duas atividades em execução simultaneamente;
* substituir a API Key por autenticação completa;
* criar múltiplos usuários de negócio;
* implementar WebSocket;
* implementar Celery;
* implementar recomendação por inteligência artificial;
* permitir edição manual da posição da fila;
* apagar históricos anteriores;
* recriar filas fechadas;
* inserir novas atividades em filas de revisão;
* mover itens já concluídos;
* mover itens já pulados;
* interromper automaticamente uma atividade já iniciada porque seu cadastro foi alterado;
* excluir fisicamente eventos históricos;
* remover compatibilidade com SQLite nos testes;
* alterar as correções de timezone definidas na `SPEC-BACK-006`.

---

## 5. Terminologia

| Termo                    | Definição                                                                                                     |
| ------------------------ | ------------------------------------------------------------------------------------------------------------- |
| Escopo                   | Identificador técnico derivado da credencial ou contexto da requisição.                                       |
| Grupo selecionado        | Grupo cuja fila o usuário deseja consumir.                                                                    |
| Grupo `Todos`            | Grupo lógico padrão que permite selecionar atividades de todos os grupos.                                     |
| Ciclo normal             | Fila aleatória em que o usuário pode iniciar ou pular atividades.                                             |
| Ciclo de revisão         | Fila formada somente pelas atividades puladas no ciclo normal imediatamente anterior.                         |
| Fila ativa               | Fila ainda disponível para apresentação e consumo.                                                            |
| Fila fechada             | Fila que não possui mais itens consumíveis.                                                                   |
| Reconciliação            | Ajuste controlado de uma fila aberta após criação ou alteração de uma atividade.                              |
| Atividade elegível       | Atividade ativa que atende às regras de grupo, categoria e limite temporal.                                   |
| Tempo consumido do grupo | Soma da duração das atividades iniciadas ou concluídas no dia, conforme a regra definida nesta Specification. |

---

## 6. Decisões funcionais

### 6.1 Filas independentes

Cada combinação abaixo deve possuir sua própria fila ativa:

```text
scope_key + grupo
```

Exemplo:

```text
scope_key X + Grupo A
scope_key X + Grupo B
scope_key X + Todos
```

As três filas podem permanecer ativas simultaneamente.

Alternar entre grupos não deve:

* fechar a fila anterior;
* recriar a fila anterior;
* perder a posição da fila anterior;
* retornar item pertencente a outro grupo;
* alterar itens já apresentados em outra fila.

### 6.2 Uma execução ativa por escopo

A existência de múltiplas filas não permite múltiplas execuções simultâneas.

Deve continuar existindo no máximo:

```text
uma execução em estado preparing ou running por scope_key
```

Caso exista uma atividade em execução no Grupo A, o usuário não pode iniciar outra atividade no Grupo B ou em `Todos`.

### 6.3 Grupo `Todos`

O grupo marcado com:

```python
is_default=True
```

representa o grupo lógico `Todos`.

Quando nenhum grupo for informado, o backend deve normalizar a solicitação para o grupo default.

Para `Todos`:

* a fila deve incluir atividades de todos os grupos;
* a fila deve ser independente das filas dos grupos específicos;
* seu histórico de ciclo e revisão deve ser independente;
* uma atividade pode aparecer na fila de seu grupo específico e também na fila `Todos`.

A conclusão da atividade em uma fila não deve apagar automaticamente a ocorrência existente em outra fila. A elegibilidade diária deve ser reavaliada quando o item dessa outra fila chegar à apresentação.

### 6.4 Lista completa

A fila normal deve conter todas as atividades elegíveis no momento de sua geração.

Não deve existir o corte fixo:

```python
ordered_activities[:30]
```

A quantidade da fila será determinada pelas regras de elegibilidade.

### 6.5 Ordem aleatória persistida

A lista deve ser randomizada uma vez e persistida por meio de `ActivityQueueItem.position`.

Chamadas repetidas ao endpoint de próxima atividade devem retornar o mesmo item apresentado enquanto ele não for:

* iniciado;
* concluído;
* pulado;
* expirado.

O frontend não deve receber uma atividade diferente apenas por atualizar a tela.

---

## 7. Identificação da fila

## 7.1 Constraint

A constraint atual baseada somente em:

```text
scope_key
```

deve ser substituída por uma constraint condicional baseada em:

```text
scope_key + group
```

aplicada às filas com:

```text
state = active
```

Resultado esperado:

* uma fila ativa para Grupo A;
* uma fila ativa para Grupo B;
* uma fila ativa para `Todos`;
* nenhuma duplicidade ativa para o mesmo escopo e grupo.

## 7.2 Grupo obrigatório na fila

Novas filas devem possuir `group` preenchido.

Solicitações sem grupo explícito devem usar o grupo default.

A migration deve tratar filas legadas com `group=NULL`.

A estratégia de migração deve:

1. localizar ou criar o grupo default;
2. preencher filas legadas sem grupo com o grupo default;
3. resolver possíveis conflitos de filas ativas do mesmo escopo e grupo;
4. preservar preferencialmente a fila ativa mais recente ou aquela que contém execução aberta;
5. fechar ou cancelar de maneira controlada as filas conflitantes;
6. somente depois aplicar a nova constraint.

A migration não deve apagar históricos ou itens.

---

## 8. Geração da fila normal

A fila normal deve considerar:

* atividade ativa;
* categoria válida;
* grupo selecionado;
* grupo `Todos`;
* limite diário da categoria;
* limite diário de tempo do grupo;
* atividade já concluída no dia;
* atividade premium;
* regras de ponderação vigentes;
* atividades que se tornaram inelegíveis.

Fluxo:

```text
normalizar grupo
→ localizar fila ativa do escopo e grupo
→ reconciliar itens
→ devolver item existente
ou
→ fechar fila esgotada
→ verificar se existe revisão obrigatória
→ gerar revisão
ou
→ gerar novo ciclo normal
```

---

## 9. Ciclo normal

Durante o ciclo normal, o usuário pode:

* iniciar a atividade;
* pular a atividade.

Ao iniciar:

1. validar que o item pertence ao escopo e ao grupo;
2. validar que o item está apresentável;
3. validar que não existe outra execução aberta;
4. criar o `Schedule`;
5. criar ou vincular o `History`;
6. marcar o item como iniciado;
7. seguir o contador persistente;
8. marcar o item como concluído quando o tempo terminar.

Ao pular:

1. validar o escopo;
2. validar a fila;
3. validar que a fila permite pulo;
4. marcar o item como pulado;
5. registrar o evento correspondente;
6. atualizar o contador da fila;
7. apresentar o próximo item.

---

## 10. Registro das atividades realizadas

A tabela `History` continuará sendo o registro operacional das atividades iniciadas.

Uma atividade será considerada concluída somente quando possuir evidência equivalente a:

```text
Schedule.state = completed
History.end_time preenchido
ActivityQueueItem.state = completed
```

Relatórios de atividades efetivamente realizadas não devem considerar registros abertos ou incompletos como conclusão.

A conclusão deve manter consistência transacional entre:

* `Schedule`;
* `History`;
* `ActivityQueueItem`;
* `ActivityPreferenceEvent`;
* contadores da fila.

---

## 11. Registro das atividades não realizadas

Uma atividade pulada deve permanecer registrada por meio de:

* `ActivityQueueItem.state = skipped`;
* `ActivityQueueItem.skipped_at`;
* `ActivityPreferenceEvent.event_type = skipped`;
* associação com a fila e o grupo correspondentes.

O saldo de atividades puladas deve ser calculado por:

```text
scope_key + grupo + ciclo normal de origem
```

Não deve ser calculado globalmente apenas pelo `scope_key`.

Atividades puladas no Grupo A não podem gerar revisão no Grupo B.

Atividades puladas no Grupo A não podem gerar revisão em `Todos`, e vice-versa.

---

## 12. Revisão obrigatória das atividades puladas

### 12.1 Momento de criação

Quando todos os itens consumíveis da fila normal tiverem sido:

* concluídos;
* pulados;
* expirados;

a fila normal deve ser fechada.

Se houver pelo menos uma atividade pulada nesse ciclo normal, o backend deve criar imediatamente uma fila:

```text
mode = skipped_review
skip_locked = true
```

Não deve mais existir a regra:

```text
criar revisão somente depois de cinco filas normais
```

### 12.2 Conteúdo da revisão

A fila de revisão deve conter somente as atividades puladas na fila normal imediatamente anterior do mesmo:

```text
scope_key + grupo
```

Cada ocorrência pulada deve gerar no máximo uma ocorrência na revisão correspondente.

A ordem da revisão pode ser aleatória, mas deve ser persistida depois de criada.

### 12.3 Proibição de novo pulo

Durante a revisão:

* o endpoint de pulo deve retornar conflito;
* o item deve permanecer apresentado;
* o backend não deve avançar a fila;
* o frontend poderá somente iniciar a atividade;
* o contrato atual de erro `skip_locked` deve ser preservado.

### 12.4 Conclusão da revisão

Quando uma atividade da revisão for concluída, deve ser registrado:

```text
skipped_completed
```

Esse evento deve liquidar o saldo de pulo referente ao ciclo correspondente.

Depois que todos os itens da revisão forem concluídos ou expirados:

1. fechar a fila de revisão;
2. encerrar o ciclo;
3. permitir a geração de uma nova fila normal para o mesmo grupo.

Não deve ser criada uma revisão de revisão.

---

## 13. Relação entre ciclo normal e revisão

A fila de revisão deve possuir referência à fila normal que a originou.

A implementação deve adicionar uma associação equivalente a:

```text
source_queue
```

em `ActivityQueue`, com relação opcional para outra fila.

Regras:

* fila normal: `source_queue=NULL`;
* fila de revisão: `source_queue` aponta para a fila normal;
* somente uma revisão ativa ou fechada deve existir para uma fila normal;
* a relação deve permitir auditoria do ciclo;
* a exclusão de uma fila não deve apagar silenciosamente sua origem;
* preferir `PROTECT` ou outra estratégia segura.

Essa referência evita depender apenas da contagem global de eventos para descobrir quais pulos pertencem à revisão atual.

---

## 14. Reconciliação de atividades criadas e alteradas

## 14.1 Princípio

A fila é um snapshot persistido, mas permite reconciliação controlada enquanto estiver ativa.

A reconciliação não deve reembaralhar toda a fila.

Ela deve preservar:

* posições já consumidas;
* item atualmente iniciado;
* histórico;
* estados finais;
* ordem relativa dos itens que já existiam.

## 14.2 Nova atividade

Quando uma atividade ativa for criada, ela deve ser avaliada para inclusão em:

* fila normal ativa de seu grupo;
* fila normal ativa do grupo `Todos`.

A atividade não deve ser incluída em:

* fila de revisão;
* fila fechada;
* fila cancelada;
* fila cuja categoria já atingiu o limite diário;
* fila cujo grupo já atingiu o limite diário de tempo;
* fila em que a atividade já exista.

## 14.3 Atividade ativada

Quando uma atividade passar de inativa para ativa, deve seguir a mesma regra de uma nova atividade.

## 14.4 Mudança de grupo ou categoria

Quando a categoria ou o grupo efetivo da atividade for alterado:

* itens pendentes ou apresentados em filas onde ela deixou de ser elegível devem ser expirados;
* itens iniciados devem permanecer válidos até sua conclusão;
* itens concluídos ou pulados não devem ser alterados;
* a atividade deve ser avaliada para inclusão nas novas filas normais elegíveis;
* a fila `Todos` deve permanecer coerente;
* não deve ser criado item duplicado.

## 14.5 Desativação

Quando uma atividade for desativada:

* itens pendentes devem ser expirados;
* itens apresentados e ainda não iniciados devem ser expirados;
* itens iniciados devem permanecer até conclusão;
* itens concluídos e pulados devem permanecer históricos;
* a atividade não deve entrar em novas filas.

## 14.6 Alteração de nome, descrição ou duração

Alterações cadastrais devem refletir na serialização por meio da referência atual à atividade.

A fila não precisa recriar o item.

Se a duração for alterada:

* execução ainda não iniciada usa a nova duração;
* execução já iniciada mantém `expected_end_at` calculado no início;
* não recalcular retroativamente o término de uma execução ativa.

## 14.7 Inserção aleatória

A nova atividade deve ser inserida aleatoriamente entre as posições ainda não consumidas.

A implementação deve:

1. bloquear a fila para atualização;
2. identificar os itens pendentes posteriores ao item apresentado;
3. escolher uma posição válida aleatória;
4. deslocar posições posteriores com segurança;
5. preservar a constraint de posição única;
6. atualizar `pool_size`;
7. evitar duplicidade por fila e atividade.

Deve ser adicionada uma constraint equivalente a:

```text
queue + activity
```

caso o domínio confirme que uma atividade deve ocorrer apenas uma vez em cada fila.

---

## 15. Regras de tempo e quantidade

## 15.1 Duração da atividade

`Activity.duration` continua representando a duração, em minutos, de uma execução.

Esse valor é usado para calcular:

```text
expected_end_at
```

## 15.2 Limite da categoria

`Category.max_daily_executions` continua representando a quantidade máxima diária de inícios permitidos para atividades da categoria.

Para esta Specification, uma execução consome o limite da categoria quando é efetivamente iniciada.

Atividades apenas apresentadas ou puladas não consomem o limite.

## 15.3 Limite diário do grupo

O modelo `Group` deve receber um campo equivalente a:

```python
max_daily_minutes = models.PositiveIntegerField(
    default=0,
    help_text='Limite diário em minutos. Zero significa sem limite.',
)
```

Regra:

* `0` significa ilimitado;
* valor maior que zero limita o total diário do grupo;
* para `Todos`, o limite deve ser aplicado somente se configurado explicitamente;
* o tempo deve ser apurado com base nas atividades iniciadas no dia;
* uma execução aberta deve reservar sua duração integral;
* atividades puladas não consomem tempo;
* atividades somente apresentadas não consomem tempo;
* uma atividade não pode ser iniciada caso sua duração ultrapasse o saldo restante do grupo.

Exemplo:

```text
limite do grupo = 120 minutos
tempo consumido/reservado = 90 minutos
próxima atividade = 60 minutos
resultado = atividade inelegível para início
```

A fila pode expirar o item quando ele não puder mais ser executado naquele dia.

## 15.4 Elegibilidade

Uma atividade será elegível quando:

```text
ativa
E categoria válida
E pertence ao grupo solicitado ou a fila é Todos
E categoria ainda permite execução
E grupo ainda possui saldo temporal
E atividade ainda cabe no saldo temporal do grupo
E não foi concluída no dia, conforme regra vigente
```

---

## 16. Diversidade e ponderação

O acaso deve continuar sendo o principal mecanismo de ordenação.

Atividades premium podem continuar recebendo prioridade conforme a regra vigente.

Entretanto, o peso histórico não deve produzir concentração indefinida em atividades repetidas.

A implementação deve revisar a ponderação para garantir que:

* todas as atividades elegíveis permaneçam na fila;
* peso altera ordem, mas não remove atividade;
* conclusão anterior não faz a atividade dominar permanentemente ciclos futuros;
* pulo não elimina permanentemente a atividade;
* revisão de puladas não conta como nova preferência positiva comum;
* a mesma atividade não apareça duas vezes na mesma fila.

Não é necessário implementar machine learning.

---

## 17. Contratos HTTP

## 17.1 Próxima atividade

```http
GET /api/activities/next/?group_id={group_id}
```

ou contrato equivalente já existente.

A resposta deve identificar:

* atividade;
* `queue_item_id`;
* fila;
* grupo;
* modo da fila;
* se o pulo está bloqueado;
* posição atual;
* tamanho da fila;
* quantidade consumida.

Exemplo conceitual:

```json
{
  "queue_id": 10,
  "queue_item_id": 101,
  "group_id": 3,
  "group_name": "Jogos",
  "mode": "normal",
  "skip_locked": false,
  "position": 4,
  "pool_size": 18,
  "consumed_count": 3,
  "activity": {
    "id": 8,
    "name": "Path of Exile 2",
    "duration": 60
  }
}
```

A implementação deve preservar os campos já utilizados pelo frontend.

Campos novos devem ser aditivos.

## 17.2 Pular

```http
POST /api/activity-queue-items/{queue_item_id}/skip/
```

O endpoint deve:

* validar escopo;
* validar grupo por meio da fila;
* permitir pulo somente em modo normal;
* ser idempotente para item já pulado;
* rejeitar item concluído;
* rejeitar item iniciado;
* retornar `skip_locked` durante revisão.

## 17.3 Iniciar

```http
POST /api/activities/{activity_id}/start/
```

Payload vigente:

```json
{
  "queue_item_id": 101
}
```

O endpoint deve:

* validar a associação atividade-item;
* validar escopo;
* validar fila ativa;
* validar limites da categoria;
* validar limite temporal do grupo;
* preservar a unicidade de execução aberta por escopo;
* manter idempotência;
* preservar compatibilidade com a `SPEC-BACK-006`.

---

## 18. Concorrência e transações

Operações de fila devem utilizar transações.

Devem ser protegidas contra concorrência:

* criação de fila;
* busca de fila ativa;
* apresentação de item;
* pulo;
* início;
* conclusão;
* fechamento;
* criação da revisão;
* inserção aleatória;
* expiração de item;
* atualização das posições.

Usar, quando aplicável:

```python
transaction.atomic()
select_for_update()
```

Duas requisições simultâneas não podem:

* criar duas filas ativas para o mesmo escopo e grupo;
* apresentar dois itens diferentes;
* inserir a mesma atividade duas vezes;
* criar duas revisões para o mesmo ciclo;
* iniciar duas atividades;
* duplicar evento de pulo ou conclusão.

---

## 19. Migrações

A implementação deve criar migrations para:

* nova constraint de fila ativa;
* normalização do grupo;
* referência `source_queue`;
* limite diário de minutos no grupo;
* eventual unicidade entre fila e atividade;
* índices necessários para consultas por escopo, grupo, estado e modo.

A migration deve ser segura para:

* PostgreSQL;
* banco com dados existentes;
* execução repetida do deploy;
* filas legadas;
* eventos legados;
* ambiente de testes SQLite.

Nenhuma migration deve apagar registros de histórico como solução para conflito.

---

## 20. Arquivos inicialmente afetados

A análise deve confirmar, mas são esperadas alterações em:

```text
apps/pomodoro/models.py
apps/pomodoro/serializers.py
apps/pomodoro/views.py
apps/pomodoro/tests.py
apps/pomodoro/admin.py
apps/pomodoro/services/activity_queue.py
apps/pomodoro/services/activity_execution.py
apps/pomodoro/migrations/
docs/specs/SPEC-BACK-003_FILA_RANDOMIZACAO_ATIVIDADES.md
docs/specs/SPEC-BACK-007_FILAS_PERSISTENTES_POR_GRUPO.md
docs/postman/backend_pomodoro_task.postman_collection.json
```

Caso o nome real da `SPEC-BACK-003` seja diferente, atualizar o arquivo existente sem criar uma cópia duplicada.

Pode ser criado um serviço específico, por exemplo:

```text
apps/pomodoro/services/activity_queue_reconciliation.py
```

somente se a separação melhorar clareza, testes e responsabilidade.

---

## 21. Testes obrigatórios

### 21.1 Filas por grupo

Criar testes que comprovem:

1. Grupo A possui fila própria;
2. Grupo B possui fila própria;
3. `Todos` possui fila própria;
4. as três filas podem permanecer ativas;
5. trocar de grupo preserva o item apresentado;
6. Grupo B nunca recebe item exclusivo da fila do Grupo A;
7. não é possível criar duas filas ativas para o mesmo escopo e grupo.

### 21.2 Lista completa

Criar mais de 30 atividades elegíveis e comprovar que:

* todas entram na fila;
* não existe corte em 30;
* não há atividades duplicadas;
* todas possuem posições únicas.

### 21.3 Pulo e revisão

Comprovar:

1. item pode ser pulado na fila normal;
2. evento de pulo é criado;
3. terminar a fila normal cria imediatamente a revisão;
4. revisão contém somente itens pulados daquele ciclo;
5. pulo da revisão retorna `skip_locked`;
6. conclusão da revisão cria `skipped_completed`;
7. concluir a revisão permite novo ciclo normal;
8. não é criada revisão quando não houve pulo;
9. pulo do Grupo A não aparece na revisão do Grupo B;
10. pulo do Grupo A não aparece na revisão de `Todos`.

### 21.4 Execução

Comprovar:

* uma única execução aberta por escopo;
* fila de outro grupo não permite iniciar simultaneamente;
* concluir atividade libera o próximo início;
* execução ativa permanece válida após alteração cadastral da atividade;
* timezone continua compatível com PostgreSQL.

### 21.5 Reconciliação

Comprovar:

* atividade nova entra na fila normal de seu grupo;
* atividade nova entra na fila normal de `Todos`;
* atividade não entra na revisão;
* atividade não é duplicada;
* atividade ativada é incluída;
* atividade desativada expira itens não iniciados;
* mudança de grupo remove logicamente da fila antiga;
* mudança de grupo inclui na fila nova;
* item iniciado não é invalidado;
* posições permanecem únicas depois da inserção;
* ordem já consumida não é modificada.

### 21.6 Limites

Comprovar:

* limite de categoria considera atividade iniciada;
* pulo não consome limite da categoria;
* grupo com limite zero é ilimitado;
* grupo com limite configurado bloqueia excesso;
* execução aberta reserva a duração;
* item que não cabe no saldo é tratado como inelegível;
* filas de grupos diferentes calculam seus próprios limites;
* `Todos` respeita seu limite quando configurado.

### 21.7 Concorrência e idempotência

Comprovar, dentro do possível:

* duas chamadas para próxima atividade retornam o mesmo item;
* dois pulos não duplicam eventos;
* dois inícios não criam duas execuções;
* duas reconciliações não duplicam item;
* duas requisições não criam duas revisões.

---

## 22. Validações técnicas

Executar, no mínimo:

```bash
poetry run python manage.py check
poetry run python manage.py makemigrations --check
poetry run python manage.py migrate
poetry run python manage.py test
```

Quando o projeto possuir ferramentas configuradas, executar também:

```bash
poetry run ruff check .
poetry run ruff format --check .
```

A implementação deve ser validada com PostgreSQL, além do SQLite isolado dos testes.

Registrar:

* comandos executados;
* resultado;
* testes não executados;
* motivo de qualquer validação pendente.

---

## 23. Critérios de aceite

A implementação será aceita quando:

* [x] houver uma fila ativa independente por `scope_key + grupo`;
* [x] Grupo A, Grupo B e `Todos` preservarem suas próprias posições;
* [x] continuar existindo somente uma execução ativa por escopo;
* [x] a fila normal incluir todas as atividades elegíveis;
* [x] o limite fixo de 30 tiver sido removido;
* [x] a ordem permanecer persistida;
* [x] o usuário puder iniciar ou pular durante o ciclo normal;
* [x] atividades concluídas forem registradas de forma consistente;
* [x] atividades puladas forem registradas por fila, grupo e ciclo;
* [x] terminar uma fila normal com pulos criar imediatamente uma revisão;
* [x] a revisão não permitir pulo;
* [x] terminar a revisão permitir um novo ciclo normal;
* [x] não houver contaminação de pulos entre grupos;
* [x] atividades novas ou ativadas forem reconciliadas com filas normais abertas;
* [x] atividades alteradas ou desativadas forem reconciliadas com segurança;
* [x] itens iniciados não forem interrompidos indevidamente;
* [x] o limite diário de categoria continuar funcionando;
* [x] o limite diário de minutos do grupo estiver implementado;
* [x] migrations preservarem dados existentes;
* [x] contratos atuais do frontend permanecerem compatíveis;
* [x] testes cobrirem o fluxo completo;
* [x] validação em PostgreSQL for registrada;
* [x] documentação técnica for atualizada;
* [x] um commit Git coeso for criado.

---

## 24. Riscos

### 24.1 Migração de filas legadas

Pode haver mais de uma fila historicamente incompatível com a nova constraint.

A migration deve resolver conflitos sem apagar histórico.

### 24.2 Alteração de posições

Inserção no meio de uma fila com constraint de posição única pode provocar colisão temporária.

A atualização deve utilizar estratégia transacional segura.

### 24.3 Grupo `Todos`

A mesma atividade pode existir simultaneamente na fila específica e na fila `Todos`.

Isso é permitido, mas sua elegibilidade deve ser revalidada antes da apresentação ou início.

### 24.4 Eventos históricos

Eventos anteriores não possuem referência explícita ao ciclo funcional além da fila.

A nova implementação deve usar `source_queue` para ciclos novos e preservar os eventos legados.

### 24.5 PostgreSQL versus SQLite

Constraints condicionais, locking e concorrência podem se comportar de modo diferente.

A validação exclusiva no SQLite não é suficiente.

---

## 25. Documentação relacionada

```text
docs/handoff/02_handoff_analise_projeto.md
docs/specs/FILA_RANDOMIZACAO_ATIVIDADES.md
docs/specs/ATIVIDADE_ATIVA_PERSISTENTE_MULTIPLATAFORMA.md
docs/specs/CONTADOR_ASSINCRONO_FRONTEND.md
docs/specs/SPEC-BACK-006_CORRECAO_INICIO_EXECUCAO_TIMEZONE_POSTGRES.md
```

A implementação deve verificar os nomes reais dos arquivos e atualizar referências quebradas.

---

## 26. Histórico

| Data       | Alteração                                                                    |
| ---------- | ---------------------------------------------------------------------------- |
| 2026-07-12 | Criação da Specification com base na análise funcional e técnica do backend. |
| 2026-07-12 | Implementação concluída com migration `0014`, reconciliação explícita e validação em SQLite e PostgreSQL 16. |

## 27. Implementação confirmada

A implementação usa `ActivityQueue(group, scope_key)` como identidade da fila ativa e normaliza a ausência de grupo para o registro `is_default=True`. A revisão usa `source_queue` one-to-one com `PROTECT`, é criada no encerramento da fila normal e nunca origina outra revisão.

O serviço `activity_queue_reconciliation.py` é chamado explicitamente após criação ou atualização pela API e pelo Django Admin. Inserções ocorrem somente na região pendente, com deslocamento transacional temporário para respeitar a unicidade de posição no PostgreSQL.

O tempo diário reservado é a soma de `Activity.duration` dos históricos iniciados no dia. Como `History` nasce no início da execução, execuções abertas reservam a duração integral; apresentação e pulo não reservam tempo. `max_daily_minutes=0` significa ausência de limite.

Contratos aditivos da fila:

```text
queue_group_id
source_queue_id
```

O serializer de grupo também expõe `max_daily_minutes`. Os campos anteriores consumidos pelo frontend foram preservados.

Validação registrada em 2026-07-12:

```text
SQLite isolado: 55 testes aprovados
PostgreSQL 16 efêmero: migrations 0001-0014 e 55 testes aprovados
```
