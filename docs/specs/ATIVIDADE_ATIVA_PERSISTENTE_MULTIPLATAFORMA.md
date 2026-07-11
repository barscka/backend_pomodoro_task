---

spec_id: SPEC-BACK-004
titulo: Atividade ativa persistente multiplataforma
status: APPROVED
fase: AS_IS
situacao: VIGENTE
responsavel: Arquitetura de Software
criado_em: 2026-07-07
atualizado_em: 2026-07-11
documento_principal:

* SPEC-BACK-004
  dependencias:
* SPEC-BACK-001
* SPEC-BACK-002
* SPEC-BACK-003
  substitui: []
  substituida_por: []

---

# SPEC-BACK-004 — Atividade ativa persistente multiplataforma

## 1. Objetivo

Definir o comportamento canônico da atividade ativa persistente no backend Pomodoro.

A execução ativa deve ser a mesma em todos os clientes que utilizem o mesmo escopo técnico.

Quando uma atividade for iniciada em um cliente, outro cliente deve conseguir descobrir:

* qual atividade está ativa;
* qual fila originou a execução;
* qual item da fila está em execução;
* quando a execução foi solicitada;
* quando a atividade começou;
* quando deve terminar;
* quanto tempo resta segundo o servidor;
* qual estado está vigente;
* qual versão do estado está persistida.

O backend deve ser a fonte de verdade da execução ativa e do tempo restante.

---

## 2. Estado da Specification

### Classificação geral

**PARCIALMENTE CONFIRMADO**

Está confirmado nos fontes atuais que:

* a fila persistida foi implementada;
* `queue_id` e `queue_item_id` fazem parte do fluxo;
* existe serviço específico de execução;
* o início persiste uma execução;
* existe `scope_key`;
* existe constraint para limitar execuções abertas;
* existem estados persistidos;
* existe controle de versão;
* existem campos temporais completos;
* existe endpoint para consultar execução ativa;
* existe endpoint de status da execução;
* existe reconciliação;
* o tempo restante é calculado no backend;
* o início valida a relação entre atividade e item da fila;
* a conclusão atualiza a execução e o item da fila;
* o contrato permite sincronização entre clientes.

Permanecem parcialmente confirmados:

* restauração real entre mobile e desktop;
* polling periódico a cada cinco minutos;
* uso de relógio monotônico no frontend;
* remoção completa da autoridade local;
* conclusão automática com todos os clientes fechados;
* scheduler dedicado;
* rotina periódica independente de chamadas HTTP;
* comportamento definitivo de execução vencida em `GET active`;
* homologação do fluxo completo em PostgreSQL após a `SPEC-BACK-006`.

---

## 3. Contexto

Antes desta evolução, a atividade ativa dependia parcialmente do armazenamento local do cliente.

Esse desenho apresentava os seguintes problemas:

* outro dispositivo não conhecia a execução;
* refresh podia perder a referência;
* mobile e desktop podiam apresentar estados diferentes;
* o contador dependia do cliente que iniciou;
* a execução ativa não podia ser descoberta sem ID salvo;
* o frontend podia concluir a atividade por conta própria;
* uma falha local podia deixar o backend inconsistente.

A execução ativa persistente foi criada para centralizar estado, prazo e identidade da atividade no backend.

---

## 4. Dependência com a fila persistida

Esta Specification depende diretamente da:

```text
SPEC-BACK-003
```

A fila precisa existir antes da execução ativa persistente porque ela define:

* `queue_id`;
* `queue_item_id`;
* estados do item;
* item apresentado;
* item iniciado;
* item concluído;
* item pulado;
* regras de concorrência;
* contrato do endpoint de início;
* relação entre execução e atividade apresentada.

Sem a fila, seria possível preservar o contador, mas não garantir que todos os clientes estivessem sincronizados com a mesma atividade apresentada.

---

## 5. Escopo

Esta Specification contempla:

* persistência da atividade ativa;
* identidade da execução;
* vínculo com fila;
* vínculo com item da fila;
* timestamps completos;
* controle de versão;
* escopo técnico;
* unicidade da execução aberta;
* endpoint de início;
* endpoint de execução ativa;
* endpoint de status;
* endpoint ou serviço de reconciliação;
* cálculo de tempo restante;
* sincronização entre clientes;
* comportamento de retomada;
* tratamento de conflito;
* integração com conclusão;
* integração com histórico;
* integração com eventos de preferência;
* compatibilidade entre mobile e desktop.

---

## 6. Fora de escopo

Não fazem parte desta Specification:

* WebSocket;
* Server-Sent Events;
* FCM;
* APNs;
* Web Push;
* autenticação completa de usuários;
* múltiplas execuções simultâneas;
* cancelamento da execução;
* pausa e retomada;
* alteração da atividade depois do início;
* alteração da duração depois do início;
* reescrita da fila;
* alteração dos pesos;
* recomendação por machine learning;
* troca de banco;
* troca de framework;
* correção específica de timezone em `TimeField`;
* implantação de scheduler distribuído.

A correção de timezone no início e na conclusão pertence à:

```text
SPEC-BACK-006
```

---

## 7. Autoridade temporal

O backend deve ser a única autoridade para:

* iniciar a execução;
* definir o instante de início;
* definir o prazo final;
* determinar o estado;
* calcular o tempo restante;
* concluir a execução;
* reconciliar uma execução vencida;
* persistir o histórico;
* atualizar o item da fila.

O frontend pode atualizar o contador visual localmente, mas não pode alterar o estado de negócio apenas com base no seu próprio relógio.

---

## 8. Modelo temporal

A execução deve armazenar timestamps completos com timezone.

Campos canônicos:

| Campo             | Finalidade                               |
| ----------------- | ---------------------------------------- |
| `requested_at`    | Instante da solicitação de início.       |
| `starts_at`       | Instante de início efetivo da atividade. |
| `expected_end_at` | Prazo final calculado pelo backend.      |
| `completed_at`    | Instante da conclusão.                   |
| `state`           | Estado persistido.                       |
| `version`         | Controle de atualização.                 |
| `scope_key`       | Escopo técnico da execução.              |
| `queue_item_id`   | Item da fila em execução.                |

Campos legados de data e hora podem permanecer por compatibilidade, mas não devem ser a única fonte do instante real.

---

## 9. Estados

Estados identificados no domínio:

```text
preparing
running
completed
cancelled
expired
```

### 9.1 `preparing`

A execução foi criada, mas a atividade ainda não começou.

Condição:

```text
server_now < starts_at
```

### 9.2 `running`

A atividade está em execução.

Condição:

```text
starts_at <= server_now < expected_end_at
```

### 9.3 `completed`

A execução foi concluída.

### 9.4 `cancelled`

Estado reservado para cancelamento explícito.

O fluxo completo não foi confirmado.

### 9.5 `expired`

Estado técnico para execução vencida ou inconsistente.

O uso operacional efetivo deve seguir o serviço atual.

---

## 10. Escopo da execução

O aplicativo é pessoal e utilizado por um único usuário.

O backend mantém `scope_key` para:

* identificar o contexto técnico;
* impedir duas execuções abertas;
* associar execução e fila;
* suportar idempotência;
* permitir descoberta da execução ativa;
* preservar compatibilidade futura.

O escopo atual não representa um usuário de negócio completo.

Ele pode ser derivado da API Key ou da instalação.

---

## 11. Constraint de execução aberta

Deve existir no máximo uma execução aberta por escopo.

Constraint esperada:

```python
models.UniqueConstraint(
    fields=["scope_key"],
    condition=(
        Q(state__in=["preparing", "running"])
        & ~Q(scope_key="")
    ),
    name="unique_open_schedule_per_scope",
)
```

A proteção deve existir também no serviço por meio de:

* transação;
* `select_for_update`;
* revalidação após lock;
* tratamento de `IntegrityError`;
* resposta idempotente ou conflito estável.

---

## 12. Vínculo com o item da fila

Toda execução iniciada pelo fluxo de fila deve possuir `queue_item_id`.

Antes de iniciar, o backend deve validar:

* item existente;
* item pertencente à fila correta;
* item pertencente ao escopo;
* atividade do item igual à atividade solicitada;
* item em estado iniciável;
* item ainda não consumido.

Se o item não corresponder à atividade:

```http
409 Conflict
```

```json
{
  "code": "queue_item_mismatch",
  "detail": "A atividade informada não corresponde ao item da fila."
}
```

---

## 13. Contrato de início

## 13.1 Endpoint

```http
POST /api/activities/{activity_id}/start/
```

Payload:

```json
{
  "queue_item_id": 91
}
```

O campo `group_id` não substitui `queue_item_id`.

---

## 13.2 Nova execução

Quando uma execução for criada:

```http
201 Created
```

Exemplo conceitual:

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
  "requested_at": "2026-07-07T13:00:00Z",
  "starts_at": "2026-07-07T13:00:00Z",
  "expected_end_at": "2026-07-07T13:25:00Z",
  "completed_at": null,
  "remaining_seconds": 1500,
  "server_now": "2026-07-07T13:00:00Z",
  "version": 1,
  "status": "started"
}
```

---

## 13.3 Repetição idempotente

Quando a mesma atividade e o mesmo item já tiverem execução aberta:

```http
200 OK
```

A resposta deve reutilizar a execução existente.

Não deve:

* criar outro `Schedule`;
* criar outro `History`;
* alterar o horário inicial;
* consumir outro item;
* criar outro evento;
* retornar `304`.

---

## 13.4 Conflito com outra atividade

Quando houver outra atividade ativa no mesmo escopo:

```http
409 Conflict
```

Exemplo:

```json
{
  "code": "active_execution_conflict",
  "detail": "Já existe uma atividade em execução.",
  "active_execution": {
    "execution_id": 42,
    "queue_item_id": 91,
    "state": "running"
  }
}
```

O frontend deve sincronizar a execução existente.

---

## 14. Endpoint de execução ativa

## 14.1 Endpoint

```http
GET /api/activities/active/
```

## 14.2 Execução ativa existente

Resposta:

```http
200 OK
```

O payload deve usar o mesmo schema da execução iniciada.

## 14.3 Sem execução ativa

Contrato esperado:

```http
204 No Content
```

O comportamento real deve ser confirmado nos testes e serializer atuais.

Não deve haver alternância entre `204`, `404` e `200` vazio sem documentação.

---

## 15. Endpoint de status

## 15.1 Endpoint

```http
GET /api/activity-executions/{execution_id}/
```

ou rota equivalente vigente.

A resposta deve recalcular:

```text
remaining_seconds
```

com base em:

```text
expected_end_at - server_now
```

O endpoint deve utilizar o mesmo serializer do início e da execução ativa sempre que possível.

---

## 16. Reconciliação

A reconciliação atualiza o estado real da execução.

Pode ser exposta por:

```http
POST /api/activity-executions/{execution_id}/reconcile/
```

Payload opcional:

```json
{
  "known_version": 1
}
```

Respostas esperadas:

* `200 OK` com estado atualizado;
* `404 Not Found` quando a execução não estiver disponível;
* `409 Conflict` quando houver conflito de versão relevante.

A reconciliação também pode ocorrer implicitamente em:

* `GET active`;
* `GET status`;
* rotina periódica;
* job agendado.

---

## 17. Execução vencida

Quando:

```text
server_now >= expected_end_at
```

e a execução ainda estiver aberta, o backend deve reconciliá-la.

A operação deve:

1. bloquear a execução;
2. confirmar o estado;
3. concluir somente uma vez;
4. preencher `completed_at`;
5. atualizar `History`;
6. atualizar o item da fila;
7. criar evento positivo;
8. atualizar contadores;
9. incrementar versão;
10. impedir duplicidade.

---

## 18. Conclusão

Ao concluir:

```text
started → completed
```

O backend deve atualizar, na mesma operação lógica:

* `Schedule`;
* `History`;
* `ActivityQueueItem`;
* `ActivityPreferenceEvent`;
* contadores da fila;
* versão da execução.

A conclusão repetida deve ser idempotente.

---

## 19. Conclusão automática

A conclusão automática pode ocorrer por:

* job agendado;
* reconciliação HTTP;
* rotina periódica.

A existência de reconciliação HTTP está confirmada.

A conclusão totalmente independente dos clientes permanece não comprovada porque não foi identificado scheduler dedicado operacional.

---

## 20. Scheduler

### Estado

**NÃO LOCALIZADO**

Não foi confirmada a existência de:

* processo dedicado;
* serviço `scheduler` no Compose;
* job por execução;
* job store persistente;
* rotina periódica autônoma;
* recuperação de jobs após restart.

A presença de dependências de APScheduler não comprova funcionamento operacional.

Enquanto não houver essa evidência, uma execução vencida pode depender de nova chamada HTTP para ser reconciliada.

---

## 21. Cálculo do tempo restante

Regra canônica:

```text
remaining_seconds =
    max(0, expected_end_at - server_now)
```

O backend deve retornar:

* `server_now`;
* `expected_end_at`;
* `remaining_seconds`.

Não deve persistir um contador decrementado por segundo.

---

## 22. Sincronização entre clientes

Todo cliente deve consultar a execução ativa:

* ao iniciar;
* ao retomar do background;
* ao abrir a tela do contador;
* após conflito de início;
* após mismatch;
* após erro de sincronização;
* periodicamente enquanto o contador estiver visível.

O frontend não deve depender de:

* `SharedPreferences`;
* `schedule_id` salvo;
* cache local;
* memória da sessão.

Esses recursos podem ser cache, mas não fonte de verdade.

---

## 23. Reconciliação periódica

A referência original desta Specification define:

```text
5 minutos
```

como intervalo de reconciliação.

O intervalo deve ser considerado valor operacional configurável.

A propriedade funcional obrigatória é:

* existir reconciliação periódica enquanto a execução estiver visível;
* evitar polling por segundo;
* corrigir drift;
* detectar conclusão externa;
* evitar timers duplicados.

A `SPEC-BACK-005` utiliza uma referência de 30 segundos para maior precisão visual.

Essa divergência deve ser resolvida no frontend:

* cinco minutos prioriza menor tráfego;
* trinta segundos prioriza menor defasagem;
* o backend não depende desse intervalo para concluir.

O valor definitivo deve ser registrado em uma Specification de frontend ou configuração única.

---

## 24. Relógio do frontend

O frontend deve projetar o tempo usando:

```text
estimated_server_now =
    server_now + tempo_monotônico_decorrido
```

Não deve depender somente de:

```text
DateTime.now()
```

porque o relógio local pode mudar.

Ao sincronizar, o valor do backend substitui a projeção local.

---

## 25. Comportamento offline

Quando o cliente ficar offline:

* pode continuar projetando o último prazo;
* deve indicar que o estado pode estar desatualizado;
* não deve concluir localmente;
* não deve iniciar outra atividade;
* deve reconciliar ao restaurar conexão.

O snapshot local não possui autoridade.

---

## 26. Pulo e execução ativa

Um item em execução não pode ser pulado pelo endpoint normal da fila.

O endpoint de pulo deve retornar conflito equivalente a:

```http
409 Conflict
```

```json
{
  "code": "active_execution_running",
  "detail": "O item já está em execução."
}
```

Cancelar uma execução ativa requer endpoint e regra separados, fora do escopo.

---

## 27. Compatibilidade com preparação

A preparação pode existir como estado persistido.

Nesse caso:

```text
requested_at < starts_at < expected_end_at
```

O frontend deve exibir:

* preparação até `starts_at`;
* execução entre `starts_at` e `expected_end_at`.

A decisão atual deve seguir os campos e constantes implementados no backend.

Não deve existir preparação somente local.

---

## 28. Contrato temporal

Toda resposta temporal deve incluir, quando aplicável:

```text
execution_id
queue_id
queue_item_id
state
requested_at
starts_at
expected_end_at
completed_at
remaining_seconds
server_now
version
```

Todos os timestamps devem ser serializados em RFC 3339.

UTC com `Z` é o formato preferencial.

---

## 29. Controle de versão

O campo:

```text
version
```

deve ser incrementado quando houver mudança relevante de estado.

Pode ser usado para:

* detectar resposta desatualizada;
* evitar atualização concorrente;
* reconciliar cache;
* identificar mudança entre clientes.

Não deve ser incrementado a cada simples consulta.

---

## 30. Concorrência

Cenários que devem ser protegidos:

1. dois starts simultâneos;
2. start e skip simultâneos;
3. duas reconciliações;
4. job e reconciliação;
5. duas conclusões;
6. dois clientes atualizando a mesma execução;
7. criação concorrente de fila e execução.

Mecanismos:

* `transaction.atomic`;
* `select_for_update`;
* constraints condicionais;
* `IntegrityError`;
* idempotência;
* verificação de versão;
* revalidação após lock.

---

## 31. Arquivos relacionados

| Caminho relativo                               | Responsabilidade                             |
| ---------------------------------------------- | -------------------------------------------- |
| `apps/pomodoro/models.py`                      | Persistência da execução, fila e histórico.  |
| `apps/pomodoro/services/activity_execution.py` | Início, consulta, reconciliação e conclusão. |
| `apps/pomodoro/services/activity_queue.py`     | Fila e item.                                 |
| `apps/pomodoro/views.py`                       | Contratos HTTP.                              |
| `apps/pomodoro/serializers.py`                 | Serialização temporal.                       |
| `apps/pomodoro/urls.py`                        | Rotas.                                       |
| `apps/pomodoro/tests.py`                       | Testes.                                      |
| `compose.yml`                                  | Infraestrutura.                              |
| `SPEC-BACK-001`                                | PostgreSQL.                                  |
| `SPEC-BACK-002`                                | Contrato de início.                          |
| `SPEC-BACK-003`                                | Fila persistida.                             |
| `SPEC-BACK-005`                                | Contador e frontend.                         |
| `SPEC-BACK-006`                                | Correção temporal.                           |

---

## 32. Testes obrigatórios do backend

### Início

* grava `requested_at`;
* grava `starts_at`;
* grava `expected_end_at`;
* grava `queue_item_id`;
* grava `scope_key`;
* retorna `201`;
* repetição retorna `200`;
* não duplica execução;
* conflito retorna `409`.

### Execução ativa

* outro cliente no mesmo escopo encontra a execução;
* cliente sem cache encontra a execução;
* sem execução retorna contrato estável;
* execução vencida é reconciliada;
* resposta inclui tempo restante.

### Status

* retorna schema canônico;
* recalcula tempo;
* preserva horário inicial;
* retorna versão;
* não altera estado sem necessidade.

### Reconciliação

* execução vencida conclui;
* execução ainda válida permanece aberta;
* repetição é idempotente;
* item da fila é atualizado;
* histórico é concluído;
* evento não duplica;
* versão é incrementada.

### Concorrência

* dois starts não criam duas execuções;
* start de outra atividade retorna conflito;
* duas conclusões não duplicam dados;
* job e reconciliação resultam em uma conclusão.

### PostgreSQL

* constraint parcial funciona;
* locks funcionam;
* tempo é persistido;
* campos temporais são compatíveis após `SPEC-BACK-006`.

---

## 33. Testes esperados do frontend

* mobile inicia e desktop restaura;
* desktop inicia e mobile restaura;
* refresh não reinicia contador;
* resume consulta o backend;
* polling corrige drift;
* conflito sincroniza execução existente;
* mismatch força nova sincronização;
* offline não conclui localmente;
* retorno da conexão reconcilia;
* conclusão atualiza a próxima atividade;
* armazenamento local não é obrigatório;
* timer não duplica;
* relógio local incorreto não altera a regra.

---

## 34. Validação manual

1. garantir fila ativa;
2. obter item;
3. iniciar no cliente A;
4. confirmar execução persistida;
5. abrir cliente B sem cache;
6. confirmar mesma atividade;
7. confirmar mesmo `queue_item_id`;
8. aguardar período de reconciliação;
9. verificar ajuste de tempo;
10. fechar ambos os clientes;
11. ultrapassar `expected_end_at`;
12. confirmar conclusão automática ou posterior reconciliação;
13. validar histórico;
14. validar item concluído;
15. buscar próxima atividade.

---

## 35. Critérios de aceite

A Specification será considerada atendida quando:

1. atividade ativa for persistida;
2. todos os clientes encontrarem a mesma execução;
3. `queue_item_id` permanecer associado;
4. tempo restante vier do backend;
5. refresh não reiniciar contador;
6. outra atividade não iniciar no mesmo escopo;
7. start repetido for idempotente;
8. execução vencida for reconciliada;
9. conclusão atualizar fila e histórico;
10. frontend não depender de cache;
11. frontend não concluir localmente;
12. polling corrigir divergências;
13. fluxo funcionar em PostgreSQL;
14. erro atual de início estiver corrigido;
15. mobile e desktop forem homologados.

Para conclusão automática integral:

16. scheduler deve estar operacional;
17. jobs devem ser persistentes;
18. execução deve concluir com todos os clientes fechados.

---

## 36. Definition of Done

A Specification somente estará integralmente concluída quando:

* fila estiver implementada;
* execução ativa estiver implementada;
* constraint estiver aplicada;
* endpoints estiverem estáveis;
* reconciliação estiver implementada;
* conclusão estiver integrada;
* testes passarem;
* PostgreSQL estiver validado;
* frontend estiver sincronizado;
* mobile e desktop estiverem homologados;
* scheduler dedicado estiver operacional;
* conclusão sem clientes estiver comprovada;
* documentação estiver atualizada.

---

## 37. Pendências

Permanecem pendentes:

* confirmar intervalo definitivo de polling;
* validar mobile e desktop;
* analisar o frontend Flutter;
* confirmar comportamento de `GET active` após vencimento;
* confirmar scheduler dedicado;
* confirmar serviço no Compose;
* comprovar conclusão sem clientes;
* validar restart do scheduler;
* validar clock skew;
* validar execução em PostgreSQL após `SPEC-BACK-006`;
* documentar payload real dos serializers;
* confirmar uso efetivo do estado `preparing`;
* confirmar uso efetivo de `cancelled` e `expired`.

---

## 38. Divergências corrigidas nesta revisão

| Informação anterior                                   | Estado atualizado                      |
| ----------------------------------------------------- | -------------------------------------- |
| Documento dependia de implementação futura da fila    | A fila está implementada.              |
| Execução ativa era apenas proposta                    | Estrutura e endpoints existem.         |
| `scope_key` era decisão pendente                      | Foi implementado.                      |
| `version` era proposta                                | Foi implementado.                      |
| `GET active` era apenas planejado                     | Endpoint identificado.                 |
| Reconciliação era apenas planejada                    | Serviço ou endpoint identificado.      |
| Modelo ainda não estava definido                      | `Schedule` foi evoluído como execução. |
| Spec era TO-BE                                        | Atualizada para AS-IS parcial.         |
| Scheduler foi tratado como parte esperada             | Continua não comprovado.               |
| Sincronização multiplataforma foi tratada como pronta | Continua pendente de homologação.      |

---

## 39. Documentação relacionada

| Documento       | Relação                                  |
| --------------- | ---------------------------------------- |
| `SPEC-BACK-001` | Banco PostgreSQL e timezone.             |
| `SPEC-BACK-002` | Contrato de início idempotente.          |
| `SPEC-BACK-003` | Fila persistida.                         |
| `SPEC-BACK-005` | Contador persistente e frontend.         |
| `SPEC-BACK-006` | Correção da falha de início e conclusão. |

---

## 40. Histórico

| Data       | Versão | Alteração                                                                                                      | Responsável             |
| ---------- | -----: | -------------------------------------------------------------------------------------------------------------- | ----------------------- |
| 2026-07-07 |    1.0 | Criação da Specification de atividade ativa persistente multiplataforma.                                       | Arquitetura de Software |
| 2026-07-07 |    1.1 | Definição de escopo, timestamps, reconciliação, endpoints e integração com fila.                               | Arquitetura de Software |
| 2026-07-11 |    2.0 | Inclusão do identificador canônico `SPEC-BACK-004` e atualização de plano TO-BE para referência AS-IS parcial. | Arquitetura de Software |
| 2026-07-11 |    2.1 | Registro da execução ativa, `scope_key`, versão, endpoints e reconciliação como itens confirmados.             | Arquitetura de Software |
| 2026-07-11 |    2.2 | Registro da sincronização mobile/desktop e scheduler como itens pendentes de comprovação.                      | Arquitetura de Software |
| 2026-07-11 |    2.3 | Inclusão da relação com a `SPEC-BACK-006` para correção do fluxo temporal no PostgreSQL.                       | Arquitetura de Software |

---

## 41. Conclusão

A execução ativa persistente está incorporada ao backend.

O domínio atual possui base para:

* manter uma única execução aberta;
* associá-la a um item da fila;
* recuperar o estado;
* calcular tempo restante;
* reconciliar execução vencida;
* sincronizar clientes;
* preservar versão;
* impedir starts concorrentes.

A arquitetura principal desta Specification foi implementada.

A conclusão integral depende de:

* correção da `SPEC-BACK-006`;
* homologação mobile e desktop;
* definição do polling;
* comprovação do scheduler;
* validação de conclusão com todos os clientes fechados.
