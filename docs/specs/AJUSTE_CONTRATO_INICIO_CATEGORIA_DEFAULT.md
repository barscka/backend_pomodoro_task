---

spec_id: SPEC-BACK-002
titulo: Contrato de início de atividades e categoria padrão
status: APPROVED
fase: AS_IS
situacao: VIGENTE
responsavel: Arquitetura de Software
criado_em: 2026-06-22
atualizado_em: 2026-07-11
documento_principal:

* SPEC-BACK-002
  dependencias:
* SPEC-BACK-001
  substitui: []
  substituida_por: []

---

# SPEC-BACK-002 — Contrato de início de atividades e categoria padrão

## 1. Objetivo

Definir o contrato canônico de início de atividades e as invariantes da categoria padrão do domínio Pomodoro.

Esta Specification estabelece que:

* requisições `POST` de início nunca devem retornar `304 Not Modified`;
* uma execução nova deve retornar `201 Created`;
* uma execução já existente pode ser reutilizada de forma idempotente;
* a reutilização deve retornar uma resposta HTTP compatível com operações `POST`;
* a categoria `Todos`, de ID fixo `1`, é a categoria padrão do domínio;
* toda atividade deve possuir categoria;
* novas atividades sem categoria informada devem utilizar a categoria padrão;
* a categoria padrão não pode ser removida enquanto sustentar a integridade do domínio.

---

## 2. Estado da Specification

### Classificação geral

**PARCIALMENTE CONFIRMADO**

Está confirmado nos fontes atuais que:

* a categoria padrão possui ID fixo `1`;
* existe rotina de criação ou obtenção da categoria padrão;
* atividades devem possuir categoria;
* a relação com categoria é obrigatória no modelo vigente;
* a exclusão da categoria referenciada é protegida;
* a migration de normalização da categoria padrão foi criada;
* o endpoint de início não utiliza mais `304 Not Modified`;
* o fluxo de início foi posteriormente integrado à fila persistida;
* o backend possui tratamento de idempotência para execução aberta.

Permanece pendente de validação operacional:

* o início completo no PostgreSQL após a correção da `SPEC-BACK-006`;
* a resposta exata utilizada em todos os cenários de reutilização;
* a aderência integral do frontend ao payload vigente;
* a confirmação de que a migration foi executada em todos os ambientes existentes.

---

## 3. Contexto

A primeira versão do backend permitia:

* `Activity.category = NULL`;
* exclusão de categoria usando `SET_NULL`;
* atividades sem categoria padrão;
* retorno `304 Not Modified` em um `POST` de início quando já existia agendamento aberto.

Esses comportamentos apresentavam dois problemas principais.

### 3.1 Contrato HTTP inadequado

O status:

```http
304 Not Modified
```

é destinado à validação de cache em operações condicionais, normalmente `GET` e `HEAD`.

Ele não representa corretamente a reutilização idempotente de uma operação `POST`.

Além disso, clientes e proxies podem ignorar o corpo de respostas `304`, impedindo a leitura do identificador da execução existente.

### 3.2 Categoria opcional

Atividades sem categoria causavam inconsistências porque:

* a regra de elegibilidade dependia da categoria;
* o limite diário era definido pela categoria;
* a seleção da próxima atividade podia encontrar registros incompletos;
* serializações podiam retornar `category: null`;
* a exclusão de categoria podia deixar atividades sem classificação.

A decisão adotada foi transformar `Todos`, de ID `1`, na categoria padrão obrigatória.

---

## 4. Escopo

Esta Specification contempla:

* contrato HTTP do início de atividades;
* idempotência da operação de início;
* respostas de criação e reutilização;
* categoria padrão `Todos`;
* ID canônico da categoria padrão;
* migração dos registros existentes;
* realocação da categoria anteriormente localizada no ID `1`;
* consolidação de categoria `Todos` preexistente;
* preenchimento de atividades sem categoria;
* obrigatoriedade da relação entre atividade e categoria;
* proteção da categoria padrão;
* testes de contrato;
* testes de integridade da migration;
* relação com o fluxo posterior de fila persistida.

---

## 5. Fora de escopo

Não fazem parte desta Specification:

* alterar a lógica de randomização;
* alterar o histórico de itens pulados;
* definir os pesos da fila;
* alterar o tamanho das pools;
* implementar autenticação multiusuário;
* separar dados por usuário;
* alterar o grupo padrão;
* exigir que o grupo padrão tenha ID `1`;
* reformular toda a modelagem de `Schedule`;
* permitir múltiplas execuções simultâneas no mesmo escopo;
* corrigir incompatibilidades temporais do PostgreSQL;
* alterar o contador visual do frontend;
* alterar TLS, CORS ou transporte HTTP;
* executar migrations diretamente em produção sem procedimento de release.

A correção temporal do início e da conclusão está documentada separadamente na:

```text
SPEC-BACK-006
```

---

## 6. Contrato do endpoint de início

## 6.1 Endpoint original

O contrato originalmente definido nesta Specification utilizava:

```http
POST /api/activities/{activity_id}/start/
```

Após a implementação da fila persistida, o endpoint passou a depender também do item apresentado ao frontend.

Payload vigente esperado:

```json
{
  "queue_item_id": 91
}
```

O `queue_item_id` associa a execução ao item efetivamente apresentado pela fila.

---

## 6.2 Nova execução

Quando não existe uma execução aberta reutilizável, o backend deve:

1. validar a atividade;
2. validar o item da fila;
3. criar o `Schedule`;
4. associar o item da fila;
5. criar ou inicializar o histórico correspondente;
6. alterar o estado do item da fila;
7. retornar `201 Created`.

Exemplo conceitual:

```http
201 Created
```

```json
{
  "execution_id": 123,
  "queue_id": 10,
  "queue_item_id": 91,
  "state": "running",
  "remaining_seconds": 1500,
  "version": 1,
  "status": "started",
  "activity": {
    "id": 4,
    "name": "Estudar Python"
  }
}
```

O payload exato deve seguir o serializer e os contratos vigentes do backend.

---

## 6.3 Execução existente

Quando já existir uma execução aberta compatível com o escopo atual, o backend pode reutilizá-la.

A resposta deve:

* nunca usar `304`;
* retornar o identificador da execução;
* manter payload compatível com o frontend;
* não criar outro `Schedule`;
* não criar outro histórico;
* não consumir outro item da fila;
* não reiniciar indevidamente o contador;
* preservar a versão da execução.

O status HTTP canônico para reutilização é:

```http
200 OK
```

Exemplo conceitual:

```json
{
  "execution_id": 123,
  "queue_item_id": 91,
  "state": "running",
  "remaining_seconds": 1425,
  "version": 1,
  "status": "already_started"
}
```

---

## 6.4 Idempotência

A idempotência do início deve considerar a execução aberta vigente.

Uma repetição da requisição não pode:

* duplicar o `Schedule`;
* duplicar o `History`;
* incrementar contadores duas vezes;
* alterar a atividade iniciada;
* consumir dois itens da fila;
* alterar o horário inicial original;
* criar versões desnecessárias.

A resposta deve permitir que o frontend sincronize a tela com o estado persistido.

---

## 6.5 Respostas proibidas

O endpoint não deve retornar:

```http
304 Not Modified
```

em nenhuma resposta ao `POST`.

Também não deve utilizar `204 No Content` quando o frontend necessita dos dados da execução para sincronização.

---

## 7. Erros funcionais

Falhas conhecidas devem utilizar respostas controladas.

Exemplos:

| Situação                           |                                              Status esperado |
| ---------------------------------- | -----------------------------------------------------------: |
| Atividade inexistente              |                                              `404 Not Found` |
| Item da fila inexistente           | `404 Not Found` ou `409 Conflict`, conforme contrato vigente |
| Item incompatível com a atividade  |                                               `409 Conflict` |
| Item já consumido                  |                                               `409 Conflict` |
| Outra execução incompatível aberta |                                               `409 Conflict` |
| Limite diário atingido             |                                               `409 Conflict` |
| Atividade inativa                  |                                               `409 Conflict` |
| Payload inválido                   |                                            `400 Bad Request` |

Cada erro deve possuir um código funcional estável.

Exemplo:

```json
{
  "code": "daily_limit_reached",
  "detail": "O limite diário da categoria foi atingido."
}
```

---

## 8. Categoria padrão

## 8.1 Identidade canônica

A categoria padrão do domínio é:

| Campo                  | Valor                                                         |
| ---------------------- | ------------------------------------------------------------- |
| `id`                   | `1`                                                           |
| `name`                 | `Todos`                                                       |
| `description`          | Categoria padrão para atividades sem classificação específica |
| `group`                | Grupo vigente com `is_default=True`                           |
| `executions_today`     | `0`, quando o campo ainda existir                             |
| `max_daily_executions` | Valor definido pela migration ou regra vigente do projeto     |

Grupo e categoria são entidades distintas.

A categoria padrão não exige que o grupo padrão também possua ID `1`.

---

## 8.2 Invariantes

Devem ser preservadas as seguintes invariantes:

1. existe uma categoria padrão com ID `1`;
2. o nome funcional dessa categoria é `Todos`;
3. toda atividade possui categoria;
4. novas atividades sem categoria explícita usam ID `1`;
5. nenhuma atividade válida deve possuir `category_id = NULL`;
6. a categoria referenciada não pode ser excluída;
7. a categoria padrão deve pertencer ao grupo padrão vigente;
8. a resolução do grupo deve usar `is_default=True`;
9. não podem existir duas categorias equivalentes chamadas `Todos`;
10. a criação do registro padrão deve ser segura em banco vazio e banco legado.

---

## 8.3 Modelo vigente

A relação deve ser equivalente a:

```python
category = models.ForeignKey(
    Category,
    on_delete=models.PROTECT,
    default=DEFAULT_CATEGORY_ID,
    null=False,
    blank=False,
)
```

O nome da constante pode variar, mas o ID padrão deve estar centralizado.

No projeto atual foi identificado o uso de:

```python
DEFAULT_CATEGORY_ID = 1
```

---

## 8.4 Criação de atividade

Quando o payload de criação não informar categoria, a nova atividade deve ser persistida com:

```text
category_id = 1
```

O comportamento deve funcionar tanto por:

* ORM;
* serializer;
* Django Admin;
* fixtures;
* scripts internos.

O backend não deve depender apenas do frontend para fornecer a categoria.

---

## 8.5 Exclusão

A exclusão de uma categoria referenciada deve ser bloqueada.

A política adotada é:

```python
on_delete=models.PROTECT
```

Isso protege:

* a categoria padrão;
* demais categorias associadas;
* integridade das atividades existentes.

A exclusão da categoria de ID `1` deve falhar enquanto existirem referências ou enquanto ela for necessária para o default do model.

---

## 9. Migração dos dados legados

## 9.1 Estado anterior

No SQLite legado, o ID `1` era ocupado por uma categoria de negócio diferente de `Todos`.

A migration precisou preservar:

* todos os campos da categoria existente;
* atividades relacionadas;
* grupo relacionado;
* limites e configurações;
* identidade funcional da categoria.

---

## 9.2 Realocação

Quando outra categoria ocupava o ID `1`, a migration deveria:

1. localizar a categoria;
2. calcular um novo ID livre;
3. criar cópia integral no novo ID;
4. atualizar as FKs das atividades;
5. validar a atualização;
6. remover o registro antigo de ID `1`;
7. criar a categoria `Todos` com ID `1`.

Não deveria ser feita alteração direta de PK dependendo de `ON UPDATE CASCADE`.

---

## 9.3 Categoria `Todos` preexistente

Caso já existisse uma categoria `Todos` em outro ID, a migration deveria:

1. localizar o registro;
2. mover suas atividades para o ID `1`;
3. preservar os valores funcionais necessários;
4. eliminar duplicidade;
5. garantir unicidade do nome.

---

## 9.4 Atividades sem categoria

A migration deveria atualizar:

```sql
category_id IS NULL
```

para:

```text
category_id = 1
```

antes de alterar o campo para obrigatório.

Após a migration, a seguinte condição deve ser verdadeira:

```sql
SELECT COUNT(*)
FROM pomodoro_activity
WHERE category_id IS NULL;
```

Resultado esperado:

```text
0
```

---

## 9.5 Grupo padrão

A categoria `Todos` deve ser associada ao grupo que possui:

```text
is_default = true
```

A migration deve falhar ou tratar explicitamente quando:

* não existe grupo padrão;
* existem vários grupos padrão;
* o estado está inconsistente.

Não deve assumir um ID físico fixo para o grupo.

---

## 9.6 Sequence

Após inserções e realocações explícitas de PK, a sequence deve ser validada no PostgreSQL.

O próximo ID gerado precisa ser superior ao maior ID existente.

A migration ou o procedimento de deploy deve executar o ajuste necessário.

---

## 10. Estado atual da implementação

## 10.1 Confirmado nos fontes

Estão confirmados:

* constante para categoria padrão;
* criação ou obtenção da categoria padrão;
* relação obrigatória de `Activity` com `Category`;
* política de exclusão protegida;
* migration de normalização;
* fluxo de início retornando `200` ou `201`, sem `304`;
* serviço de execução separado das views;
* execução persistida;
* associação com item da fila;
* estados de execução;
* tratamento de conflitos conhecidos.

## 10.2 Evoluções posteriores

Após esta Specification, o domínio recebeu:

* fila persistida;
* pré-carregamento de atividades;
* ação de pular;
* histórico de itens pulados;
* seleção ponderada;
* execução ativa persistente;
* sincronização do frontend;
* controle de versão;
* reconciliação de execução.

Por isso, o payload atual do endpoint de início é mais amplo que o originalmente documentado nesta Specification.

---

## 11. Divergências corrigidas nesta revisão

| Informação anterior                         | Estado atualizado                                                |
| ------------------------------------------- | ---------------------------------------------------------------- |
| Estado “pronto para implementação”          | Implementação existente nos fontes.                              |
| `Activity.category` ainda aceitava `NULL`   | Relação obrigatória implementada.                                |
| Categoria padrão ainda inexistente          | Categoria padrão implementada por migration e helper.            |
| Endpoint ainda retornava `304`              | Fluxo atual não utiliza `304`.                                   |
| Início criava apenas `Schedule` e `History` | Fluxo atual também associa e atualiza item da fila.              |
| Payload usava apenas `schedule_id`          | Contrato atual utiliza dados de execução, fila, estado e versão. |
| Frontend dependia apenas de atividade e dia | Início atual também depende de `queue_item_id`.                  |
| Spec era plano TO-BE                        | Documento atualizado para referência AS-IS.                      |

---

## 12. Arquivos relacionados

| Caminho relativo                                                         | Responsabilidade                                        |
| ------------------------------------------------------------------------ | ------------------------------------------------------- |
| `apps/pomodoro/models.py`                                                | Models de categoria, atividade, execução e histórico.   |
| `apps/pomodoro/views.py`                                                 | Endpoint HTTP de início.                                |
| `apps/pomodoro/services/activity_execution.py`                           | Regra transacional de início e reutilização.            |
| `apps/pomodoro/services/activity_queue.py`                               | Seleção e apresentação dos itens da fila.               |
| `apps/pomodoro/migrations/`                                              | Migration de categoria padrão e normalização dos dados. |
| `apps/pomodoro/tests.py`                                                 | Testes de contrato e domínio.                           |
| `config/settings/test.py`                                                | Banco isolado da suíte.                                 |
| `docs/specs/SPEC-BACK-001_MIGRACAO_SQLITE_POSTGRES.md`                   | Migração para PostgreSQL.                               |
| `docs/specs/SPEC-BACK-003_FILA_RANDOMIZACAO_ATIVIDADES.md`               | Fila e seleção ponderada.                               |
| `docs/specs/SPEC-BACK-004_ATIVIDADE_ATIVA_PERSISTENTE.md`                | Persistência da execução ativa.                         |
| `docs/specs/SPEC-BACK-006_CORRECAO_INICIO_EXECUCAO_TIMEZONE_POSTGRES.md` | Correção da falha atual de início.                      |

O Codex deve usar os nomes reais dos arquivos no repositório quando atualizar links internos.

---

## 13. Testes obrigatórios

## 13.1 Contrato HTTP

Validar que:

* primeira chamada retorna `201`;
* segunda chamada compatível retorna `200`;
* nenhuma chamada retorna `304`;
* ambas retornam o mesmo identificador de execução;
* a segunda chamada não duplica o `Schedule`;
* a segunda chamada não duplica o histórico;
* a segunda chamada não consome outro item;
* o payload possui estado, versão e dados necessários para sincronização.

---

## 13.2 Categoria padrão

Validar que:

* banco vazio cria `Todos` com ID `1`;
* ID `1` ocupado é realocado;
* campos da categoria realocada são preservados;
* atividades da categoria realocada são preservadas;
* `Todos` em outro ID é consolidada;
* atividades sem categoria passam a usar ID `1`;
* novas atividades sem categoria usam ID `1`;
* exclusão da categoria associada é bloqueada;
* nenhuma atividade é serializada com categoria nula;
* próxima categoria criada não colide com IDs existentes.

---

## 13.3 Migração

Validar:

* execução em banco vazio;
* execução sobre cópia representativa do banco legado;
* integridade das FKs;
* contagens antes e depois;
* sequence no PostgreSQL;
* ausência de categorias duplicadas;
* ausência de atividades sem categoria;
* existência única da categoria padrão.

---

## 13.4 PostgreSQL

Além da suíte em SQLite, deve existir validação em PostgreSQL para:

* migration;
* sequence;
* criação de atividade;
* início de atividade;
* reutilização de execução;
* persistência de horários;
* constraints;
* transações.

A validação específica dos campos temporais pertence à `SPEC-BACK-006`.

---

## 14. Critérios de aceite

A Specification é considerada funcionalmente atendida quando:

1. nenhum `POST` de início retorna `304`;
2. execução nova retorna `201`;
3. execução reutilizada retorna `200`;
4. a reutilização não duplica dados;
5. o identificador da execução é retornado;
6. existe categoria `Todos` com ID `1`;
7. toda atividade possui categoria;
8. novas atividades usam a categoria padrão quando necessário;
9. a categoria referenciada é protegida;
10. a categoria legada do ID `1` preserva dados e vínculos;
11. não existem categorias `Todos` duplicadas;
12. a sequence permanece consistente;
13. migrations executam em banco vazio;
14. migration funciona sobre banco legado representativo;
15. testes de contrato passam;
16. o início funciona em PostgreSQL após a correção da `SPEC-BACK-006`.

---

## 15. Definition of Done

A implementação é considerada concluída quando:

* models refletem a obrigatoriedade da categoria;
* migration de dados está versionada;
* categoria padrão existe;
* registros legados estão normalizados;
* contrato HTTP não usa `304`;
* idempotência está coberta por testes;
* sequence foi validada;
* suíte padrão passa;
* fluxo foi testado em PostgreSQL;
* documentação foi atualizada;
* frontend consegue sincronizar uma execução criada ou reutilizada;
* não existem divergências conhecidas entre serializer, view, serviço e Specification.

---

## 16. Riscos e mitigação

| Risco                                       | Mitigação                                      |
| ------------------------------------------- | ---------------------------------------------- |
| Perda da categoria que ocupava ID `1`       | Realocar e atualizar FKs antes da remoção.     |
| Atividades sem categoria                    | Atualização de dados antes do `null=False`.    |
| Duplicidade de `Todos`                      | Consolidação durante a migration.              |
| Colisão de sequence                         | Ajuste e teste no PostgreSQL.                  |
| Exclusão da categoria padrão                | Uso de `PROTECT`.                              |
| Retorno HTTP incompatível                   | Testes explícitos contra `304`.                |
| Duplicação em requisição repetida           | Locks, constraints e testes de idempotência.   |
| SQLite mascarar comportamento do PostgreSQL | Teste de integração PostgreSQL.                |
| Fluxo de fila divergir do contrato original | Manter relação documental com `SPEC-BACK-003`. |
| Início falhar por horário com timezone      | Aplicar `SPEC-BACK-006`.                       |

---

## 17. Pendências

Permanecem pendentes de comprovação:

* confirmar a execução da migration em todos os ambientes;
* validar sequence no PostgreSQL definitivo;
* validar início e reutilização após `SPEC-BACK-006`;
* conferir o payload exato consumido pelo frontend atual;
* validar que o frontend trata `200` e `201`;
* confirmar que nenhuma rota antiga ainda depende de `schedule_id` em formato legado;
* validar o comportamento quando a categoria padrão estiver ausente por corrupção manual;
* revisar se o limite diário da categoria `Todos` permanece adequado ao domínio.

---

## 18. Documentação relacionada

| Documento       | Relação                                                  |
| --------------- | -------------------------------------------------------- |
| `SPEC-BACK-001` | Banco operacional PostgreSQL e migração dos dados.       |
| `SPEC-BACK-003` | Fila persistida, itens apresentados e seleção ponderada. |
| `SPEC-BACK-004` | Persistência e recuperação da execução ativa.            |
| `SPEC-BACK-005` | Sincronização e contador do frontend.                    |
| `SPEC-BACK-006` | Correção de início e conclusão no PostgreSQL.            |

---

## 19. Histórico

| Data       | Versão | Alteração                                                                                                                                                                                                                | Responsável             |
| ---------- | -----: | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------- |
| 2026-06-22 |    1.0 | Criação da Specification para correção do retorno `304` e definição da categoria padrão `Todos`.                                                                                                                         | Arquitetura de Software |
| 2026-06-22 |    1.1 | Detalhamento da migration de realocação do ID `1`, consolidação de categorias e ajuste de sequences.                                                                                                                     | Arquitetura de Software |
| 2026-07-11 |    2.0 | Inclusão do identificador canônico `SPEC-BACK-002`, atualização de plano TO-BE para referência AS-IS, registro da implementação da categoria padrão e atualização do contrato de início para o fluxo de fila persistida. | Arquitetura de Software |
| 2026-07-11 |    2.1 | Inclusão da dependência operacional da `SPEC-BACK-006` para correção dos campos temporais no PostgreSQL.                                                                                                                 | Arquitetura de Software |

---

## 20. Conclusão

A alteração estrutural prevista nesta Specification foi incorporada ao backend.

O domínio atual possui:

* categoria padrão canônica;
* atividades com categoria obrigatória;
* proteção de integridade;
* migration de dados;
* contrato HTTP sem uso de `304`;
* idempotência de execução;
* integração com fila persistida;
* resposta orientada à sincronização do frontend.

A Specification permanece vigente porque define invariantes fundamentais do domínio.

A validação final do fluxo de início depende da aplicação e homologação da correção descrita na `SPEC-BACK-006`.
