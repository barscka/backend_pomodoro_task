---

spec_id: SPEC-BACK-003
titulo: Fila persistida e randomização ponderada de atividades
status: APPROVED
fase: AS_IS
situacao: VIGENTE
responsavel: Arquitetura de Software
criado_em: 2026-06-22
atualizado_em: 2026-07-11
documento_principal:

* SPEC-BACK-003
  dependencias:
* SPEC-BACK-001
* SPEC-BACK-002
  substitui: []
  substituida_por: []

---

# SPEC-BACK-003 — Fila persistida e randomização ponderada de atividades

## 1. Objetivo

Definir o comportamento canônico da fila persistida de atividades do backend Pomodoro.

A fila deve substituir a seleção aleatória independente realizada a cada chamada ao endpoint de próxima atividade.

O backend deve:

* gerar uma sequência persistida de atividades elegíveis;
* manter o item apresentado entre chamadas sucessivas;
* permitir que o usuário inicie ou pule o item apresentado;
* registrar eventos positivos e negativos de preferência;
* utilizar o histórico de atividades concluídas e puladas para influenciar filas futuras;
* fechar pools de atividades consumidas;
* gerar novas filas de maneira ponderada;
* preservar as regras vigentes de categoria, grupo, atividade ativa e limite diário;
* manter consistência transacional entre fila, execução e histórico.

---

## 2. Estado da Specification

### Classificação geral

**PARCIALMENTE CONFIRMADO**

Está confirmado nos fontes atuais que:

* existem modelos persistentes de fila;
* existe modelo para itens da fila;
* existe modelo para eventos de preferência;
* existe serviço de domínio para gerenciamento da fila;
* o endpoint de próxima atividade usa fila persistida;
* chamadas repetidas podem retornar o item apresentado ainda não consumido;
* existe ação para pular um item;
* o pulo altera o estado do item;
* o pulo registra informação utilizada na seleção futura;
* o início da atividade depende de `queue_item_id`;
* a conclusão integra execução e item da fila;
* existe escopo de fila;
* existem constraints para limitar fila ativa;
* existem estados próprios para fila e item;
* existe seleção ponderada baseada em histórico;
* existem testes relacionados à fila e execução.

Permanecem parcialmente confirmados:

* o ciclo completo de fechamento após exatamente 30 itens;
* a regra definitiva das cinco pools normais;
* a existência operacional da pool restrita a atividades puladas;
* o bloqueio de `skip` na pool de revisão;
* a distribuição estatística real dos pesos;
* o comportamento do ciclo completo em PostgreSQL;
* a homologação ponta a ponta com o frontend.

---

## 3. Contexto

Antes desta implementação, o endpoint:

```http
GET /api/activities/next/
```

selecionava diretamente uma atividade no banco.

O fluxo anterior:

1. consultava atividades ativas;
2. aplicava filtros funcionais;
3. ordenava usando prioridade e aleatoriedade;
4. retornava somente um registro;
5. descartava a ordem sorteada após a resposta.

Esse comportamento permitia que:

* uma nova chamada retornasse outra atividade;
* refresh da tela alterasse a sugestão;
* não existisse item apresentado persistido;
* não existisse histórico específico de pulo;
* atividades concluídas e puladas não influenciassem adequadamente a seleção futura;
* o frontend não possuísse um identificador estável do item apresentado.

A fila persistida foi criada para transformar a seleção aleatória em um fluxo consumível e auditável.

---

## 4. Terminologia canônica

| Termo                 | Significado                                                                              |
| --------------------- | ---------------------------------------------------------------------------------------- |
| Fila                  | Sequência persistida de atividades apresentada ao usuário.                               |
| Item da fila          | Associação entre uma atividade e sua posição ou ocorrência dentro da fila.               |
| Pool                  | Ciclo de consumo utilizado para controlar regeneração e ponderação da fila.              |
| Item pendente         | Item criado, mas ainda não apresentado ou consumido.                                     |
| Item apresentado      | Item retornado ao frontend e aguardando ação do usuário.                                 |
| Item iniciado         | Item associado a uma execução ativa.                                                     |
| Item concluído        | Item cuja atividade foi iniciada e finalizada com sucesso.                               |
| Item pulado           | Item recusado explicitamente antes do início.                                            |
| Item expirado         | Item que deixou de ser elegível após a geração da fila.                                  |
| Evento de preferência | Registro imutável que representa conclusão, pulo ou outro sinal utilizado na ponderação. |
| Escopo                | Identificador usado para separar a fila ativa e a execução vigente.                      |

---

## 5. Escopo

Esta Specification contempla:

* persistência da fila;
* persistência dos itens da fila;
* estados da fila;
* estados dos itens;
* geração inicial;
* seleção de atividades elegíveis;
* manutenção do item apresentado;
* ação de pular;
* integração com início;
* integração com conclusão;
* eventos de preferência;
* cálculo de pesos;
* geração das filas seguintes;
* fechamento de pools;
* escopo da fila;
* constraints e transações;
* idempotência;
* tratamento de itens que se tornam inelegíveis;
* contrato HTTP da próxima atividade;
* contrato HTTP de pulo;
* contrato HTTP de início vinculado à fila.

---

## 6. Fora de escopo

Não fazem parte desta Specification:

* recomendação por machine learning;
* WebSocket ou Server-Sent Events;
* autenticação multiusuário;
* alteração estrutural de `Schedule`;
* reescrita integral do histórico operacional;
* troca de framework;
* troca de banco;
* alteração do contador visual do frontend;
* criação de tela administrativa da fila;
* edição manual da ordem da fila;
* alteração dos campos temporais de `Schedule`;
* correção específica de timezone no PostgreSQL;
* criação de múltiplas sessões simultâneas para o mesmo usuário;
* alteração das regras da categoria padrão.

A correção do início e da conclusão no PostgreSQL está registrada na:

```text
SPEC-BACK-006
```

---

## 7. Arquitetura de domínio

A implementação atual utiliza os seguintes modelos principais:

```text
ActivityQueue
ActivityQueueItem
ActivityPreferenceEvent
Activity
Schedule
History
```

Relação conceitual:

```text
ActivityQueue
   └── ActivityQueueItem
          ├── Activity
          └── Schedule
                 └── History

ActivityPreferenceEvent
   ├── Activity
   ├── ActivityQueue
   └── ActivityQueueItem
```

---

## 8. Modelos persistidos

## 8.1 `ActivityQueue`

Representa a fila ativa ou encerrada de um escopo.

Responsabilidades:

* armazenar o escopo;
* controlar o estado da fila;
* registrar o modo da pool;
* manter contadores de consumo;
* registrar número da pool;
* indicar se o pulo está bloqueado;
* registrar criação e encerramento;
* garantir unicidade da fila ativa por escopo.

Estados identificados:

```text
active
closed
cancelled
```

Outros estados somente devem ser adicionados mediante nova Specification.

---

## 8.2 `ActivityQueueItem`

Representa uma ocorrência de atividade dentro de uma fila.

Estados identificados:

```text
pending
presented
started
completed
skipped
expired
```

O item deve manter, quando aplicável:

* fila;
* atividade;
* posição;
* estado;
* data de apresentação;
* data de início;
* data de conclusão;
* data de pulo;
* referência à execução.

O mesmo item não pode ser simultaneamente concluído e pulado.

---

## 8.3 `ActivityPreferenceEvent`

Registra os sinais utilizados para ponderação futura.

A estratégia adotada utiliza eventos persistidos em vez de manter somente listas mutáveis de favoritas e puladas.

Essa decisão permite:

* preservar auditoria;
* recalcular pesos;
* ajustar regras futuras;
* evitar perda de histórico;
* diferenciar pulo, conclusão e revisão;
* impedir que uma simples flag apague o histórico anterior.

Tipos funcionais esperados incluem equivalentes a:

```text
favorite_completed
skipped
skipped_completed
```

Os nomes exatos devem seguir os choices existentes no model.

---

## 9. Escopo da fila

A fila ativa é limitada por um `scope_key`.

O aplicativo é pessoal e utilizado atualmente por apenas um usuário.

Mesmo assim, o escopo é mantido porque ele permite:

* identificar a instalação ou credencial;
* evitar duas filas ativas concorrentes;
* associar execução e fila;
* preservar compatibilidade com possível evolução futura;
* impedir colisão entre requisições simultâneas.

A implementação atual deriva o escopo da credencial ou contexto da requisição.

Neste projeto, o escopo não representa usuário de negócio completo.

---

## 10. Constraint da fila ativa

Deve existir no máximo uma fila ativa por escopo.

A regra deve ser protegida no banco por constraint condicional equivalente a:

```python
models.UniqueConstraint(
    fields=["scope_key"],
    condition=Q(state="active"),
    name="unique_active_queue_per_scope",
)
```

A geração também deve tratar `IntegrityError`, pois duas requisições podem tentar criar a fila ao mesmo tempo.

---

## 11. Geração inicial da fila

Quando não existir fila ativa utilizável, o backend deve:

1. resolver o escopo;
2. resolver o grupo solicitado, quando informado;
3. consultar atividades elegíveis;
4. excluir atividades inativas;
5. tratar premium expirado;
6. respeitar o limite diário;
7. excluir atividades incompatíveis;
8. aplicar a estratégia de seleção;
9. embaralhar ou ponderar a ordem;
10. criar a fila;
11. criar seus itens;
12. apresentar o primeiro item válido.

A criação deve ocorrer dentro de transação.

A fila não deve ser gerada novamente a cada chamada enquanto houver item válido.

---

## 12. Elegibilidade das atividades

Uma atividade somente pode entrar ou permanecer apresentável quando:

* está ativa;
* pertence ao grupo permitido, quando houver filtro;
* sua categoria está válida;
* sua categoria não atingiu o limite diário;
* regras de premium foram processadas;
* não viola restrição diária vigente;
* não possui execução incompatível aberta;
* atende ao modo da pool atual.

A elegibilidade deve ser centralizada no serviço de domínio ou helper reutilizável.

Views não devem duplicar a regra.

---

## 13. Premium

Atividades premium vigentes devem continuar recebendo prioridade.

A implementação pode:

* separar primeiro o universo premium;
* aplicar peso adicional;
* priorizar premium antes da ponderação;
* randomizar dentro do conjunto premium.

O comportamento efetivamente implementado deve ser preservado enquanto não houver nova decisão funcional.

Atividade com premium expirado não deve continuar sendo tratada como premium ativo.

---

## 14. Apresentação do item

O endpoint de próxima atividade deve retornar o primeiro item válido da fila.

Enquanto esse item estiver em estado compatível com apresentação, chamadas repetidas devem retornar o mesmo item.

A simples leitura do endpoint não deve:

* pular automaticamente;
* concluir;
* iniciar;
* criar nova fila;
* alterar preferência;
* consumir mais de um item.

O estado esperado após apresentação é equivalente a:

```text
presented
```

---

## 15. Item que se torna inválido

Uma atividade pode deixar de ser válida após a fila ser gerada.

Exemplos:

* atividade desativada;
* categoria atingiu limite;
* grupo foi alterado;
* premium expirou;
* atividade já foi concluída;
* execução incompatível foi criada.

Nesse caso, o backend deve:

1. identificar a inelegibilidade;
2. marcar o item como `expired`;
3. não apresentar a atividade;
4. procurar o próximo item válido;
5. não registrar pulo;
6. não criar evento de preferência negativo.

---

## 16. Contrato de próxima atividade

## 16.1 Endpoint

```http
GET /api/activities/next/
```

Filtros podem incluir:

```text
group_id
group_name
```

## 16.2 Resposta

Exemplo conceitual:

```json
{
  "queue_id": 10,
  "queue_item_id": 91,
  "queue_mode": "normal",
  "pool_number": 3,
  "pool_size": 30,
  "consumed_count": 12,
  "skip_locked": false,
  "activity": {
    "id": 7,
    "name": "Estudar Python",
    "duration": 25,
    "category": 2,
    "group_id": 1,
    "group_name": "Todos",
    "premium": false,
    "is_premium_active": false
  }
}
```

O serializer vigente pode possuir campos adicionais ou nomes ligeiramente diferentes.

O contrato não deve remover:

```text
queue_id
queue_item_id
activity
```

sem Specification de compatibilidade.

---

## 16.3 Ausência de item

O comportamento vigente deve ser preservado.

A versão original da Specification registrava ambiguidade entre:

```http
404 Not Found
```

e:

```http
204 No Content
```

Os fontes atuais devem ser tratados como referência operacional.

Caso o backend retorne `404`, esse comportamento deve ser documentado como contrato vigente.

Não deve haver alternância arbitrária entre `404` e `204`.

---

## 17. Ação de pular

## 17.1 Endpoint

```http
POST /api/activity-queue/items/{queue_item_id}/skip/
```

## 17.2 Regras

Ao pular:

1. localizar o item;
2. bloquear o registro em transação;
3. validar se o item pode ser pulado;
4. validar se o pulo está permitido na pool;
5. alterar o estado para `skipped`;
6. registrar `skipped_at`;
7. criar evento de preferência;
8. atualizar contadores da fila;
9. localizar ou apresentar o próximo item;
10. retornar resposta estável.

Pular não deve criar:

* `Schedule`;
* `History`;
* execução ativa;
* evento positivo.

---

## 17.3 Resposta

Exemplo:

```json
{
  "queue_item_id": 91,
  "activity_id": 7,
  "state": "skipped",
  "next_queue_item_id": 92
}
```

O campo de próximo item pode ser nulo quando a fila precisar ser regenerada.

---

## 17.4 Idempotência do pulo

Quando o item já estiver `skipped`, uma chamada repetida não deve:

* criar outro evento;
* incrementar o contador novamente;
* consumir outro item indevidamente;
* alterar o horário original do pulo.

A resposta pode reutilizar o estado existente.

---

## 18. Pulo bloqueado

Quando a pool estiver em modo que não permite pular, o endpoint deve responder:

```http
409 Conflict
```

Exemplo:

```json
{
  "code": "skip_locked",
  "detail": "Esta pool permite apenas executar atividades puladas."
}
```

A existência completa desse modo permanece parcialmente confirmada.

O campo:

```text
skip_locked
```

deve ser considerado parte do contrato da fila quando estiver presente no model e serializer.

---

## 19. Início de atividade

## 19.1 Endpoint

```http
POST /api/activities/{activity_id}/start/
```

Payload:

```json
{
  "queue_item_id": 91
}
```

O `queue_item_id` é obrigatório no fluxo de fila persistida.

---

## 19.2 Validações

Antes de iniciar, o backend deve validar:

* item existente;
* item pertencente à fila ativa;
* atividade do item igual à atividade da URL;
* item ainda iniciável;
* atividade ativa;
* categoria elegível;
* ausência de execução incompatível;
* limite diário;
* escopo da execução.

Se atividade e item divergirem:

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

## 19.3 Transição de estado

Ao iniciar:

```text
presented → started
```

ou transição equivalente prevista no código.

O backend deve:

* criar ou reutilizar `Schedule`;
* associar `Schedule` ao item;
* registrar horário de início;
* preservar a posição da fila;
* não criar outro item;
* não gerar nova fila.

---

## 19.4 Falha conhecida

Existe uma regressão no fluxo de início após a migração para PostgreSQL.

O backend tenta persistir horário com timezone em `TimeField`.

Essa correção pertence à:

```text
SPEC-BACK-006
```

A falha não altera a validade arquitetural da fila, mas impede a homologação completa do fluxo `play`.

---

## 20. Conclusão de atividade

Ao concluir uma execução:

1. localizar `Schedule`;
2. bloquear registros necessários;
3. validar estado;
4. persistir conclusão;
5. atualizar `History`;
6. marcar o item como `completed`;
7. registrar evento positivo;
8. atualizar contadores da fila;
9. avaliar fechamento da pool;
10. não duplicar eventos em repetição da chamada.

Transição esperada:

```text
started → completed
```

Uma conclusão repetida deve ser idempotente.

---

## 21. Histórico operacional e histórico de preferência

O sistema mantém duas finalidades distintas.

### 21.1 Histórico operacional

Representado por:

```text
Schedule
History
```

Registra:

* início;
* término;
* duração;
* execução;
* estado operacional.

### 21.2 Histórico de preferência

Representado por:

```text
ActivityPreferenceEvent
```

Registra:

* pulo;
* conclusão positiva;
* conclusão de item anteriormente pulado;
* peso ou sinal para seleção futura.

Um pulo não deve gerar `History` operacional.

Uma conclusão pode gerar tanto histórico operacional quanto evento de preferência.

---

## 22. Pools

Uma pool representa um ciclo de consumo da fila.

O tamanho originalmente definido é:

```text
30 itens
```

Itens consumidos incluem:

```text
completed
skipped
```

Itens expirados não devem ser tratados automaticamente como preferência.

A fila deve controlar:

* `pool_number`;
* `pool_size`;
* `consumed_count`;
* modo;
* bloqueio de pulo;
* fechamento.

A implementação deve impedir que o mesmo item incremente `consumed_count` mais de uma vez.

---

## 23. Fechamento da pool

Quando o número de itens consumidos atingir o limite:

1. encerrar a fila atual;
2. registrar `closed_at`;
3. consolidar sinais de preferência;
4. calcular o modo da próxima pool;
5. gerar nova fila;
6. preservar o escopo;
7. respeitar elegibilidade atual;
8. não reutilizar itens antigos como se fossem novos.

Essa transição deve ser transacional ou protegida contra duas filas simultâneas.

---

## 24. Ponderação

A seleção futura deve considerar eventos históricos.

Referência funcional original:

| Condição                      | Peso de referência |
| ----------------------------- | -----------------: |
| Favorita recente              |                  4 |
| Favorita histórica            |                  2 |
| Neutra                        |                  1 |
| Pulada recente em pool normal |                  1 |

Os valores efetivos devem seguir o código atual.

A Specification não deve sobrescrever silenciosamente pesos implementados sem evidência.

A propriedade funcional obrigatória é:

* atividades concluídas devem possuir chance maior que atividades neutras;
* atividades neutras devem continuar tendo chance;
* atividades puladas não devem ser permanentemente excluídas;
* a seleção não deve se tornar determinística;
* premium deve continuar respeitado;
* a fila deve evitar repetição excessiva quando houver universo suficiente.

---

## 25. Histórico de pulos

O histórico de atividades puladas deve influenciar seleções futuras.

O backend deve permitir:

* identificar atividades puladas recentemente;
* identificar recorrência de pulo;
* incluir puladas em ciclos de revisão;
* reduzir ou neutralizar o sinal negativo quando forem concluídas;
* preservar eventos anteriores para auditoria.

O histórico não deve ser representado apenas pelo estado atual de um único item, pois cada fila contém uma nova ocorrência.

---

## 26. Pool de revisão

A regra funcional originalmente proposta foi:

* após cinco pools normais;
* criar uma pool de revisão;
* incluir somente atividades anteriormente puladas;
* bloquear o pulo durante essa pool.

Essa regra deve ser tratada como:

**PARCIALMENTE CONFIRMADA**

A implementação dos modelos possui estrutura compatível com:

* modos de fila;
* contagem de pools;
* bloqueio de pulo;
* histórico de puladas.

Entretanto, a análise estática realizada não comprova integralmente:

* fechamento automático das cinco pools;
* criação obrigatória da sexta;
* universo exclusivo de puladas;
* bloqueio funcional de todos os skips;
* reinício correto do ciclo.

Esses pontos exigem teste direcionado.

---

## 27. Compatibilidade com regras existentes

Devem continuar valendo:

* somente atividades ativas são apresentadas;
* premium expirado é tratado;
* filtro de grupo restringe o universo;
* categoria é obrigatória;
* limite diário permanece válido;
* execução aberta impede início incompatível;
* atividade concluída no dia segue a regra vigente;
* categoria padrão continua disponível;
* autenticação por API Key permanece vigente;
* PostgreSQL permanece como banco operacional;
* SQLite permanece isolado para testes.

---

## 28. Concorrência

A fila deve ser segura diante de requisições simultâneas.

Mecanismos esperados:

* `transaction.atomic`;
* `select_for_update`;
* constraint de fila ativa;
* tratamento de `IntegrityError`;
* idempotência;
* bloqueio do item antes de pular;
* bloqueio do item antes de iniciar;
* verificação do estado após o lock;
* prevenção de eventos duplicados.

Cenários críticos:

1. duas chamadas de `next`;
2. duas chamadas de `skip`;
3. `skip` e `start` simultâneos;
4. duas chamadas de `start`;
5. duas conclusões;
6. duas tentativas de fechar a pool;
7. duas gerações de nova fila.

---

## 29. Contratos de erro

| Código funcional          | Situação                             |
| ------------------------- | ------------------------------------ |
| `queue_item_mismatch`     | Atividade não corresponde ao item.   |
| `skip_locked`             | Pool não permite pular.              |
| `queue_item_not_found`    | Item inexistente ou indisponível.    |
| `queue_item_consumed`     | Item já concluído ou pulado.         |
| `active_execution_exists` | Existe execução incompatível aberta. |
| `daily_limit_reached`     | Categoria atingiu limite diário.     |
| `no_activity_available`   | Não existe atividade elegível.       |

Os nomes exatos devem ser comparados aos fontes atuais antes de documentar publicamente.

---

## 30. Arquivos relacionados

| Caminho relativo                               | Responsabilidade                                         |
| ---------------------------------------------- | -------------------------------------------------------- |
| `apps/pomodoro/models.py`                      | Models da fila, item, preferência, execução e histórico. |
| `apps/pomodoro/services/activity_queue.py`     | Geração, apresentação, pulo, ponderação e fechamento.    |
| `apps/pomodoro/services/activity_execution.py` | Integração entre fila e execução.                        |
| `apps/pomodoro/views.py`                       | Contratos HTTP.                                          |
| `apps/pomodoro/serializers.py`                 | Serialização da fila, atividade e execução.              |
| `apps/pomodoro/urls.py`                        | Rotas da API.                                            |
| `apps/pomodoro/migrations/`                    | Criação dos modelos e constraints.                       |
| `apps/pomodoro/tests.py`                       | Testes do domínio.                                       |
| `SPEC-BACK-001`                                | Banco PostgreSQL.                                        |
| `SPEC-BACK-002`                                | Categoria padrão e início idempotente.                   |
| `SPEC-BACK-004`                                | Execução ativa persistente.                              |
| `SPEC-BACK-005`                                | Sincronização e contador do frontend.                    |
| `SPEC-BACK-006`                                | Correção da falha no início e conclusão.                 |

---

## 31. Testes obrigatórios

### 31.1 Geração

* primeira chamada cria fila;
* apenas uma fila ativa é criada;
* itens são persistidos;
* posições são consistentes;
* atividades inelegíveis não entram;
* grupo é respeitado;
* premium é respeitado;
* chamadas simultâneas não criam duas filas.

### 31.2 Apresentação

* chamadas repetidas retornam o mesmo item;
* item passa para apresentado;
* leitura não consome;
* item inválido expira;
* próximo item válido é localizado.

### 31.3 Pulo

* item muda para `skipped`;
* evento é criado;
* contador é incrementado uma vez;
* não cria `Schedule`;
* não cria `History`;
* repetição é idempotente;
* próximo item é retornado;
* `skip_locked` retorna `409`.

### 31.4 Início

* exige `queue_item_id`;
* valida atividade correspondente;
* cria execução;
* associa item e execução;
* item muda para iniciado;
* repetição não duplica execução;
* conflito retorna código estável;
* funciona em PostgreSQL após `SPEC-BACK-006`.

### 31.5 Conclusão

* item muda para concluído;
* evento positivo é criado;
* histórico operacional é concluído;
* repetição é idempotente;
* contador é incrementado uma vez;
* fechamento da pool é avaliado.

### 31.6 Ponderação

* favoritas recebem maior probabilidade;
* neutras permanecem possíveis;
* puladas são registradas;
* seed controlada produz teste determinístico;
* distribuição não depende de `Random()` diretamente na view.

### 31.7 Ciclo de pools

* pool fecha no limite;
* nova fila é criada;
* número da pool é incrementado;
* histórico é preservado;
* após cinco pools ocorre a regra definida;
* pool de revisão contém apenas puladas, quando aplicável;
* pulo é bloqueado na revisão;
* ciclo posterior retorna ao modo normal.

### 31.8 PostgreSQL

Validar em PostgreSQL:

* constraint parcial;
* `select_for_update`;
* geração concorrente;
* pulo concorrente;
* início concorrente;
* fechamento concorrente;
* campos temporais;
* integridade transacional.

---

## 32. Critérios de aceite

A Specification será considerada atendida quando:

1. próxima atividade for obtida de fila persistida;
2. refresh não trocar o item apresentado;
3. `queue_id` for retornado;
4. `queue_item_id` for retornado;
5. o usuário puder pular;
6. o pulo for persistido;
7. o pulo influenciar filas futuras;
8. o usuário puder iniciar o item apresentado;
9. o início validar `queue_item_id`;
10. a conclusão marcar o item;
11. eventos positivos não forem duplicados;
12. eventos de pulo não forem duplicados;
13. apenas uma fila ativa existir por escopo;
14. pool fechar no limite definido;
15. nova fila usar ponderação;
16. regras de premium forem preservadas;
17. limite diário for preservado;
18. itens inválidos expirarem;
19. concorrência não criar inconsistências;
20. o fluxo funcionar no PostgreSQL;
21. o frontend conseguir consumir o contrato.

---

## 33. Definition of Done

A implementação somente será considerada integralmente concluída quando:

* models estiverem versionados;
* migrations estiverem aplicadas;
* serviços de domínio estiverem implementados;
* view não concentrar regra de negócio;
* endpoints estiverem documentados;
* fila ativa possuir constraint;
* pulo estiver implementado;
* início estiver integrado;
* conclusão estiver integrada;
* eventos estiverem persistidos;
* pools estiverem fechando corretamente;
* ponderação estiver testada;
* revisão de puladas estiver validada;
* suíte automatizada estiver passando;
* validação PostgreSQL estiver registrada;
* frontend estiver homologado;
* documentação estiver atualizada.

---

## 34. Pendências

As regras de pool fixa de 30 itens e revisão após cinco pools foram substituídas pela `SPEC-BACK-007`. A fila normal agora contém todas as atividades elegíveis, e cada fila normal com pulos cria sua revisão imediata e isolada por grupo.

Permanece pendente apenas homologação manual ponta a ponta em todos os clientes frontend; os contratos consumidos foram verificados estaticamente e preservados.

---

## 35. Divergências corrigidas nesta revisão

| Informação anterior                                   | Estado atualizado                                            |
| ----------------------------------------------------- | ------------------------------------------------------------ |
| Estado “pronto para validação antes da implementação” | Models, serviços e endpoints já existem.                     |
| Fila era apenas proposta                              | Fila persistida está implementada.                           |
| `queue_item_id` era opcional no início                | É parte obrigatória do fluxo vigente.                        |
| Pulo era apenas planejado                             | Endpoint e transição existem.                                |
| Eventos eram apenas recomendação                      | Modelo de eventos foi implementado.                          |
| Escopo estava indefinido                              | Existe `scope_key`.                                          |
| Fila ativa sem constraint definida                    | Constraint condicional foi implementada.                     |
| Estados eram apenas sugeridos                         | Estados estão incorporados ao domínio.                       |
| Ausência de histórico de pulo                         | Histórico de preferência existe.                             |
| Spec era TO-BE                                        | Atualizada para AS-IS vigente.                               |
| Ciclo completo das pools era presumido                | Mantido como parcialmente confirmado até testes específicos. |
| Pool normal limitada a 30 itens                       | Substituída por lista completa pela `SPEC-BACK-007`.           |
| Revisão após cinco pools                              | Substituída por revisão imediata pela `SPEC-BACK-007`.         |
| Fila ativa única por escopo                           | Evoluída para única por escopo e grupo.                        |

---

## 36. Documentação relacionada

| Documento       | Relação                                           |
| --------------- | ------------------------------------------------- |
| `SPEC-BACK-001` | PostgreSQL, constraints e validação transacional. |
| `SPEC-BACK-002` | Categoria padrão e contrato de início.            |
| `SPEC-BACK-004` | Execução ativa persistente.                       |
| `SPEC-BACK-005` | Sincronização do contador no frontend.            |
| `SPEC-BACK-006` | Correção temporal do início e conclusão.          |

---

## 37. Histórico

| Data       | Versão | Alteração                                                                                                                                                                   | Responsável             |
| ---------- | -----: | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------- |
| 2026-06-22 |    1.0 | Criação da proposta de fila persistida, pools, atividades favoritas, puladas e revisão.                                                                                     | Arquitetura de Software |
| 2026-06-22 |    1.1 | Definição inicial dos modelos, estados, endpoints, concorrência e testes esperados.                                                                                         | Arquitetura de Software |
| 2026-07-11 |    2.0 | Inclusão do identificador canônico `SPEC-BACK-003` e atualização de plano TO-BE para referência AS-IS com base nos models, services, endpoints e constraints implementados. | Arquitetura de Software |
| 2026-07-11 |    2.1 | Registro de que o ciclo completo das pools e a revisão de puladas permanecem parcialmente confirmados até validação direcionada.                                            | Arquitetura de Software |
| 2026-07-11 |    2.2 | Inclusão da dependência operacional da `SPEC-BACK-006` para correção do fluxo de início e conclusão no PostgreSQL.                                                          | Arquitetura de Software |

---

## 38. Conclusão

A fila persistida está incorporada ao domínio do backend.

O sistema já possui base estrutural para:

* manter uma atividade apresentada;
* permitir pulo;
* registrar preferência;
* associar execução ao item;
* gerar seleção ponderada;
* controlar pools;
* impedir múltiplas filas ativas por escopo;
* preservar histórico de consumo.

A arquitetura principal da Specification foi implementada.

O ciclo vigente está definido e validado pela `SPEC-BACK-007`. A fila original permanece como base histórica da randomização ponderada, enquanto isolamento por grupo, revisão imediata, reconciliação e limites temporais seguem a Specification posterior.
