---

spec_id: SPEC-BACK-006
titulo: Correção do início e da conclusão de atividades com PostgreSQL
status: APPROVED
fase: AS_IS
situacao: VIGENTE
responsavel: Arquitetura de Software
criado_em: 2026-07-11
atualizado_em: 2026-07-11
documento_principal:

* SPEC-BACK-006
  dependencias:
* SPEC-BACK-001
* SPEC-BACK-002
* SPEC-BACK-003
* SPEC-BACK-004
* SPEC-BACK-005
  substitui: []
  substituida_por: []

---

# SPEC-BACK-006 — Correção do início e da conclusão de atividades com PostgreSQL

## 1. Objetivo

Corrigir a regressão observada após a implementação da fila persistida de atividades, na qual o usuário recebe uma atividade pré-carregada, mas o backend falha ao iniciar a execução quando o comando de `play` é acionado.

A correção deve garantir que:

* o item apresentado pela fila possa ser iniciado norm1almente;
* o backend crie o `Schedule` sem incompatibilidade entre timezone e PostgreSQL;
* a conclusão da atividade também seja persistida sem erro de timezone;
* o frontend receba uma resposta HTTP válida e estável;
* falhas inesperadas sejam registradas de maneira rastreável;
* o comportamento seja validado no PostgreSQL, e não somente no SQLite usado nos testes automatizados;
* nenhuma regra funcional da fila, histórico de pulos ou escolha ponderada das próximas atividades seja alterada indevidamente.

---

## 2. Contexto

O backend do projeto Pomodoro utiliza atualmente:

* Django;
* Django REST Framework;
* PostgreSQL como banco operacional;
* SQLite isolado para testes automatizados;
* fila persistida de atividades;
* itens de fila com estados próprios;
* registro de atividades puladas;
* uso do histórico da fila para influenciar seleções futuras;
* execução persistida por meio de `Schedule`;
* sincronização da execução ativa com o frontend.

A nova funcionalidade de fila permite que o frontend:

1. consulte uma atividade pré-carregada;
2. apresente essa atividade ao usuário;
3. permita iniciar a atividade por meio da ação `play`;
4. permita pular a atividade;
5. registre o pulo no histórico;
6. use esse histórico para influenciar futuras filas.

Após essa implementação, o fluxo de apresentação e pulo funciona, porém o início da atividade falha.

A mensagem observada no frontend é:

```text
O backend falhou ao iniciar. A tela será sincronizada automaticamente.
```

Essa mensagem indica que o frontend tentou iniciar a atividade, mas não recebeu uma resposta de sucesso compatível com o contrato esperado.

---

## 3. Escopo

Esta Specification contempla:

* análise do fluxo de início de atividade;
* correção da criação do `Schedule`;
* correção da gravação de `start_time`;
* correção da gravação de `end_time`;
* preservação dos campos `DateTimeField` com timezone;
* tratamento controlado de falhas inesperadas;
* criação ou atualização dos testes do fluxo;
* validação específica em PostgreSQL;
* atualização da documentação técnica relacionada;
* identificação canônica das cinco Specifications já existentes;
* criação de commit Git ao final da implementação.

---

## 4. Fora de escopo

Não fazem parte desta Specification:

* alterar a lógica de randomização da fila;
* alterar os pesos de atividades feitas, favoritas ou puladas;
* alterar o tamanho das pools;
* alterar a regra da pool restrita a atividades puladas;
* alterar o contrato funcional do botão `pular`;
* criar autenticação multiusuário;
* implementar isolamento entre usuários;
* substituir a API Key atual;
* alterar a duração das atividades;
* alterar o contador visual do frontend;
* reformular o frontend Flutter;
* criar novos estados para fila, item ou execução;
* refatorar integralmente os modelos `Schedule`, `History` ou `ActivityQueue`;
* substituir PostgreSQL por outro banco;
* remover o SQLite dos testes unitários;
* implementar uma nova arquitetura de jobs assíncronos.

O aplicativo é pessoal e, no estado atual, será utilizado por apenas um usuário. Portanto, riscos de isolamento entre diferentes usuários ou API Keys não são bloqueadores desta correção.

---

## 5. Evidência do problema

### 5.1 Fluxo afetado

O fluxo esperado é:

```text
Frontend
   ↓
GET /api/activities/next/
   ↓
Backend retorna activity e queue_item_id
   ↓
Usuário aciona play
   ↓
POST /api/activities/{activity_id}/start/
   ↓
Backend cria Schedule
   ↓
Backend retorna execução iniciada
```

O comportamento observado é:

```text
Frontend
   ↓
GET /api/activities/next/
   ↓
Backend retorna activity e queue_item_id
   ↓
Usuário aciona play
   ↓
POST /api/activities/{activity_id}/start/
   ↓
Falha interna durante a criação do Schedule
   ↓
Frontend apresenta mensagem genérica de sincronização
```

### 5.2 Arquivo principal

```text
apps/pomodoro/services/activity_execution.py
```

### 5.3 Função principal

```python
start_activity()
```

### 5.4 Implementação problemática

O fluxo obtém a data e hora atual com:

```python
now = timezone.now()
```

Em seguida, cria o `Schedule` utilizando:

```python
scheduled_date=now.date(),
start_time=now.time(),
```

Quando `USE_TZ=True`, `timezone.now()` retorna um `datetime` consciente de timezone.

O valor retornado por:

```python
now.time()
```

pode preservar `tzinfo`.

O campo do model é um `TimeField`, que deve receber um horário sem timezone para persistência compatível com o PostgreSQL.

A incompatibilidade pode resultar em erro equivalente a:

```text
PostgreSQL backend does not support timezone-aware times.
```

### 5.5 Conclusão da atividade

O mesmo padrão também deve ser revisado na função responsável pela conclusão:

```python
schedule.end_time = completion_time.time()
```

Caso `completion_time` seja timezone-aware, a conclusão pode falhar mesmo depois que o início for corrigido.

---

## 6. Diagnóstico técnico

### 6.1 Classificação

**PARCIALMENTE CONFIRMADO**

Está confirmado no código que:

* `timezone.now()` é utilizado;
* o resultado de `.time()` é atribuído a campos `TimeField`;
* o banco operacional é PostgreSQL;
* os testes automatizados usam SQLite;
* o fluxo de início cria ou atualiza um `Schedule`.

A causa exata deve ser confirmada pelo traceback do backend no momento do `play`.

Entretanto, a incompatibilidade entre horário timezone-aware e `TimeField` no PostgreSQL é a hipótese técnica prioritária e deve ser reproduzida antes da implementação definitiva.

### 6.2 Diferença entre os bancos

O SQLite utilizado nos testes pode aceitar ou normalizar valores que o PostgreSQL rejeita.

Portanto, uma suíte executada exclusivamente em SQLite não é evidência suficiente para esse fluxo.

A correção deve ser validada em:

* SQLite isolado, para preservar a suíte existente;
* PostgreSQL, para comprovar o comportamento do ambiente operacional.

---

## 7. Decisão técnica

Os campos devem ser tratados conforme seu tipo.

### 7.1 Campos `DateTimeField`

Campos como:

```text
requested_at
starts_at
expected_end_at
completed_at
```

devem continuar recebendo valores timezone-aware.

Exemplo:

```python
now = timezone.now()
```

Esses campos não devem ter o timezone removido.

### 7.2 Campos `DateField`

O campo:

```text
scheduled_date
```

deve considerar a data local configurada no Django.

A data não deve ser derivada diretamente do horário UTC quando isso puder resultar no dia anterior ou seguinte para o usuário.

Usar:

```python
local_now = timezone.localtime(now)
scheduled_date = local_now.date()
```

### 7.3 Campos `TimeField`

Os campos:

```text
start_time
end_time
```

devem receber horários locais sem `tzinfo`.

Usar uma conversão explícita equivalente a:

```python
local_now.time().replace(tzinfo=None)
```

A remoção do timezone deve ocorrer somente depois da conversão para a timezone local da aplicação.

---

## 8. Alteração esperada no início

### 8.1 Arquivo

```text
apps/pomodoro/services/activity_execution.py
```

### 8.2 Função

```python
start_activity()
```

### 8.3 Comportamento esperado

A implementação deve seguir conceitualmente:

```python
now = timezone.now()
local_now = timezone.localtime(now)

schedule = Schedule.objects.create(
    activity=activity,
    scheduled_date=local_now.date(),
    start_time=local_now.time().replace(tzinfo=None),
    completed=False,
    queue_item=queue_item,
    scope_key=scope_key,
    state=Schedule.STATE_RUNNING,
    version=1,
    requested_at=now,
    starts_at=now,
    expected_end_at=expected_end_at,
)
```

O Codex deve adaptar o exemplo ao código real encontrado no repositório.

Não deve copiar o trecho de maneira mecânica caso:

* os nomes dos campos estejam diferentes;
* existam factories ou helpers;
* o `Schedule` seja criado por outro serviço;
* exista lógica transacional adicional;
* o contrato vigente utilize campos diferentes.

---

## 9. Alteração esperada na conclusão

### 9.1 Arquivo

```text
apps/pomodoro/services/activity_execution.py
```

### 9.2 Função

Localizar a função responsável pela conclusão, atualmente identificada como equivalente a:

```python
complete_schedule()
```

### 9.3 Comportamento esperado

A implementação deve converter o instante de conclusão para horário local antes de preencher o `TimeField`.

Exemplo conceitual:

```python
completion_time = timezone.now()
local_completion = timezone.localtime(completion_time)

schedule.end_time = local_completion.time().replace(tzinfo=None)
schedule.completed_at = completion_time
```

O campo `completed_at` deve permanecer timezone-aware.

---

## 10. Contrato HTTP de início

### 10.1 Endpoint

```http
POST /api/activities/{activity_id}/start/
```

### 10.2 Payload esperado

O endpoint deve continuar recebendo o identificador do item da fila:

```json
{
  "queue_item_id": 91
}
```

### 10.3 Resposta de criação

Quando uma nova execução for criada:

```http
201 Created
```

Exemplo de resposta:

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
    "id": 7,
    "name": "Estudar Python",
    "duration": 25
  }
}
```

O Codex deve preservar o contrato efetivamente implementado e validado pelas Specifications vigentes.

Não devem ser renomeados campos sem necessidade.

### 10.4 Resposta idempotente

Caso já exista uma execução aberta que possa ser reutilizada, o endpoint deve respeitar o contrato definido pela `SPEC-BACK-002`.

Não deve retornar:

```http
304 Not Modified
```

para uma requisição `POST`.

### 10.5 Falha funcional conhecida

Conflitos conhecidos devem continuar usando respostas controladas, como:

```http
409 Conflict
```

acompanhadas por código funcional estável.

### 10.6 Falha inesperada

Falhas inesperadas devem:

* retornar `500 Internal Server Error`;
* possuir payload estável;
* ser registradas com traceback no backend;
* não expor detalhes internos ao frontend;
* não registrar API Key ou cabeçalho de autorização.

Exemplo:

```json
{
  "code": "activity_start_failed",
  "detail": "Não foi possível iniciar a atividade."
}
```

O tratamento genérico não deve esconder exceções conhecidas que já possuem tratamento específico.

---

## 11. Observabilidade

### 11.1 Log de erro

Quando ocorrer uma exceção inesperada ao iniciar uma atividade, registrar:

* identificador da atividade;
* identificador do item da fila;
* identificador da fila, quando disponível;
* tipo da exceção;
* traceback;
* operação executada;
* estado da execução, quando disponível.

Não registrar:

* API Key;
* cabeçalho `Authorization`;
* senhas;
* tokens;
* payloads completos com dados sensíveis.

### 11.2 Mensagem recomendada

Exemplo conceitual:

```python
logger.exception(
    "Falha inesperada ao iniciar atividade",
    extra={
        "activity_id": activity.id,
        "queue_item_id": queue_item_id,
    },
)
```

O Codex deve verificar o padrão de logging já utilizado pelo projeto antes de criar uma nova convenção.

---

## 12. Arquivos previstos

| Caminho relativo                                                         | Alteração esperada                                                                                          |
| ------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------- |
| `apps/pomodoro/services/activity_execution.py`                           | Corrigir conversão de data e horário no início e na conclusão.                                              |
| `apps/pomodoro/views.py`                                                 | Revisar tratamento de exceções inesperadas no endpoint de início, se ainda não existir tratamento adequado. |
| `apps/pomodoro/tests.py`                                                 | Adicionar testes do início e conclusão com horários sem `tzinfo`.                                           |
| `tests/`                                                                 | Criar teste adicional em arquivo específico, caso o projeto já separe testes por domínio.                   |
| `docs/specs/SPEC-BACK-006_CORRECAO_INICIO_EXECUCAO_TIMEZONE_POSTGRES.md` | Registrar esta Specification.                                                                               |
| `docs/specs/MIGRACAO_POSTGRES.md`                                        | Adicionar identificação `SPEC-BACK-001` e referência à validação temporal no PostgreSQL.                    |
| `docs/specs/AJUSTE_CONTRATO_INICIO_CATEGORIA_DEFAULT.md`                 | Adicionar identificação `SPEC-BACK-002`.                                                                    |
| `docs/specs/FILA_RANDOMIZACAO_ATIVIDADES.md`                             | Adicionar identificação `SPEC-BACK-003`.                                                                    |
| `docs/specs/ATIVIDADE_ATIVA_PERSISTENTE_MULTIPLATAFORMA.md`              | Adicionar identificação `SPEC-BACK-004`.                                                                    |
| `docs/specs/CONTADOR_ASSINCRONO_FRONTEND.md`                             | Adicionar identificação `SPEC-BACK-005`.                                                                    |
| `README.md`                                                              | Atualizar somente se o contrato ou procedimento de validação pública estiver desatualizado.                 |

O Codex deve informar no relatório final todos os caminhos efetivamente alterados.

---

## 13. Identificação das Specifications existentes

As cinco Specifications existentes devem receber bloco YAML canônico no início de cada arquivo.

### 13.1 SPEC-BACK-001

Arquivo:

```text
docs/specs/MIGRACAO_POSTGRES.md
```

Cabeçalho:

```yaml
---
spec_id: SPEC-BACK-001
titulo: Migração do SQLite para PostgreSQL
status: APPROVED
fase: AS_IS
situacao: VIGENTE
responsavel: Arquitetura de Software
criado_em: 2026-06-21
atualizado_em: 2026-07-11
documento_principal:
  - SPEC-BACK-001
dependencias: []
substitui: []
substituida_por: []
---
```

A data original deve ser ajustada pelo Codex caso exista evidência Git de uma data diferente.

### 13.2 SPEC-BACK-002

Arquivo:

```text
docs/specs/AJUSTE_CONTRATO_INICIO_CATEGORIA_DEFAULT.md
```

Cabeçalho:

```yaml
---
spec_id: SPEC-BACK-002
titulo: Ajuste do contrato de início e da categoria padrão
status: APPROVED
fase: AS_IS
situacao: VIGENTE
responsavel: Arquitetura de Software
criado_em: 2026-06-22
atualizado_em: 2026-07-11
documento_principal:
  - SPEC-BACK-002
dependencias:
  - SPEC-BACK-001
substitui: []
substituida_por: []
---
```

### 13.3 SPEC-BACK-003

Arquivo:

```text
docs/specs/FILA_RANDOMIZACAO_ATIVIDADES.md
```

Cabeçalho:

```yaml
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
  - SPEC-BACK-003
dependencias:
  - SPEC-BACK-001
  - SPEC-BACK-002
substitui: []
substituida_por: []
---
```

### 13.4 SPEC-BACK-004

Arquivo:

```text
docs/specs/ATIVIDADE_ATIVA_PERSISTENTE_MULTIPLATAFORMA.md
```

Cabeçalho:

```yaml
---
spec_id: SPEC-BACK-004
titulo: Atividade ativa persistente multiplataforma
status: APPROVED
fase: AS_IS
situacao: VIGENTE
responsavel: Arquitetura de Software
criado_em: 2026-06-22
atualizado_em: 2026-07-11
documento_principal:
  - SPEC-BACK-004
dependencias:
  - SPEC-BACK-003
substitui: []
substituida_por: []
---
```

### 13.5 SPEC-BACK-005

Arquivo:

```text
docs/specs/CONTADOR_ASSINCRONO_FRONTEND.md
```

Cabeçalho:

```yaml
---
spec_id: SPEC-BACK-005
titulo: Contador persistente e integração assíncrona do frontend
status: APPROVED
fase: AS_IS
situacao: VIGENTE
responsavel: Arquitetura de Software
criado_em: 2026-06-22
atualizado_em: 2026-07-11
documento_principal:
  - SPEC-BACK-005
dependencias:
  - SPEC-BACK-003
  - SPEC-BACK-004
substitui: []
substituida_por: []
---
```

### 13.6 Regra de preservação documental

A inclusão dos cabeçalhos YAML não autoriza:

* reescrever integralmente as Specifications;
* alterar decisões funcionais sem evidência;
* marcar como implementado o que estiver apenas planejado;
* remover seções históricas;
* substituir títulos sem necessidade;
* alterar dependências sem analisar o conteúdo;
* considerar automaticamente todas as Specifications como concluídas.

O Codex deve comparar cada Specification com o código atual e preservar a diferença entre:

* comportamento planejado;
* comportamento implementado;
* comportamento parcialmente implementado;
* comportamento obsoleto.

Caso uma Specification esteja desatualizada, o Codex deve registrar a divergência, mas não reescrever seu conteúdo fora do escopo desta correção.

---

## 14. Substituições documentais

A `SPEC-BACK-006` não substitui integralmente nenhuma Specification anterior.

Ela:

* complementa a `SPEC-BACK-001`, porque trata uma incompatibilidade manifestada após a migração para PostgreSQL;
* complementa a `SPEC-BACK-002`, porque corrige tecnicamente o endpoint de início;
* complementa a `SPEC-BACK-003`, porque o início ocorre a partir de um item da fila persistida;
* complementa a `SPEC-BACK-004`, porque a execução ativa precisa ser criada corretamente;
* complementa a `SPEC-BACK-005`, porque o contador e a sincronização dependem de uma execução iniciada com sucesso.

Por isso:

```yaml
substitui: []
substituida_por: []
```

As Specifications anteriores não devem ser marcadas como substituídas pela `SPEC-BACK-006`.

---

## 15. Regras de implementação

O Codex deve:

1. analisar o estado atual do projeto antes de alterar qualquer arquivo;
2. confirmar pelo código qual função inicia a execução;
3. confirmar pelo código qual função conclui a execução;
4. consultar os models para identificar os tipos reais dos campos;
5. reproduzir o erro no PostgreSQL sempre que o ambiente permitir;
6. consultar os logs existentes antes de concluir a causa;
7. aplicar a menor correção necessária;
8. manter `DateTimeField` com timezone;
9. remover `tzinfo` apenas de valores destinados a `TimeField`;
10. converter o instante para horário local antes de remover `tzinfo`;
11. preservar transações e locks existentes;
12. preservar os estados da fila;
13. preservar o contrato de `queue_item_id`;
14. preservar a idempotência vigente;
15. não alterar a seleção ponderada da fila;
16. não alterar o histórico de pulos;
17. não criar migrations sem mudança de schema;
18. não alterar o frontend neste escopo;
19. adicionar testes de regressão;
20. executar todas as validações disponíveis;
21. atualizar os cabeçalhos das cinco Specifications existentes;
22. criar a nova `SPEC-BACK-006`;
23. informar divergências encontradas;
24. criar commit Git ao final.

---

## 16. Testes obrigatórios

### 16.1 Início de atividade

Validar que:

* uma atividade apresentada pela fila pode ser iniciada;
* o endpoint recebe `queue_item_id`;
* uma nova execução retorna `201`;
* o `Schedule` é criado;
* `scheduled_date` corresponde à data local;
* `start_time` é persistido sem `tzinfo`;
* `starts_at` permanece timezone-aware;
* `requested_at` permanece timezone-aware;
* `expected_end_at` permanece timezone-aware;
* o item da fila passa ao estado esperado;
* a execução fica no estado esperado;
* não é criado um segundo `Schedule` indevido;
* o histórico não é duplicado;
* o contrato da resposta permanece compatível com o frontend.

### 16.2 Conclusão

Validar que:

* uma atividade iniciada pode ser concluída;
* `end_time` é persistido sem `tzinfo`;
* `completed_at` permanece timezone-aware;
* o estado da execução é atualizado;
* o estado do item da fila é atualizado;
* o histórico é finalizado corretamente;
* a conclusão repetida é idempotente, caso esse seja o contrato vigente.

### 16.3 Fila

Validar que a correção não altera:

* apresentação do próximo item;
* ação de pular;
* registro da atividade pulada;
* escolha da próxima atividade;
* estado da fila;
* contagem de itens consumidos;
* pools existentes;
* pesos de atividades.

### 16.4 Erros

Validar que:

* conflitos funcionais continuam retornando o status previsto;
* falhas inesperadas retornam payload controlado;
* o traceback é registrado no backend;
* informações sensíveis não aparecem no log.

### 16.5 PostgreSQL

Executar ao menos um teste ou validação de integração em PostgreSQL que:

1. crie uma fila;
2. obtenha um item;
3. inicie a atividade;
4. consulte a execução ativa;
5. conclua a atividade;
6. confirme os valores persistidos.

O resultado no SQLite não substitui essa validação.

---

## 17. Validações técnicas

Executar, adaptando os comandos ao ambiente real do projeto:

```bash
poetry run python manage.py check
```

```bash
poetry run python manage.py makemigrations --check --dry-run
```

```bash
APP_ENV=test poetry run python manage.py test --settings=config.settings.test
```

Quando o projeto for executado por Docker:

```bash
docker compose run --rm backend python manage.py check
```

```bash
docker compose run --rm backend python manage.py makemigrations --check --dry-run
```

```bash
docker compose run --rm backend python manage.py test
```

Validar também os logs durante o início:

```bash
docker compose logs --tail=200 backend
```

ou:

```bash
docker compose logs -f backend
```

Caso exista ambiente PostgreSQL de desenvolvimento, executar o fluxo real por HTTP ou pela suíte de integração.

---

## 18. Teste manual do contrato

### 18.1 Obter próxima atividade

```bash
curl -sS \
  -H "Authorization: Api-Key <API_KEY>" \
  http://127.0.0.1/api/activities/next/
```

Guardar:

* `activity.id`;
* `queue_id`;
* `queue_item_id`.

### 18.2 Iniciar atividade

```bash
curl -i \
  -X POST \
  -H "Authorization: Api-Key <API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"queue_item_id": 91}' \
  http://127.0.0.1/api/activities/7/start/
```

Resultado esperado:

```http
HTTP/1.1 201 Created
```

ou resposta idempotente prevista no contrato vigente.

### 18.3 Consultar execução ativa

Executar o endpoint vigente de execução ativa e confirmar que:

* a atividade iniciada é retornada;
* o tempo restante é calculado;
* o `queue_item_id` permanece associado;
* o estado é `running` ou equivalente.

### 18.4 Concluir atividade

Executar o endpoint vigente de conclusão e confirmar:

* resposta de sucesso;
* atualização do `Schedule`;
* atualização do item da fila;
* atualização do histórico;
* inexistência de erro de timezone.

---

## 19. Critérios de aceite

A implementação será considerada aceita quando:

1. o botão `play` deixar de apresentar a mensagem de falha do backend;
2. o endpoint de início retornar sucesso;
3. o `Schedule` for criado no PostgreSQL;
4. `start_time` for persistido sem timezone;
5. `scheduled_date` usar a data local correta;
6. os campos `DateTimeField` continuarem timezone-aware;
7. a atividade puder ser concluída;
8. `end_time` for persistido sem timezone;
9. a fila continuar funcionando;
10. a ação de pular continuar funcionando;
11. o histórico de pulos continuar influenciando as seleções futuras;
12. nenhuma migration de schema desnecessária for criada;
13. os testes automatizados passarem;
14. o fluxo for validado em PostgreSQL;
15. os logs não expuserem dados sensíveis;
16. as cinco Specifications existentes receberem IDs canônicos;
17. a nova `SPEC-BACK-006` for adicionada;
18. o Codex apresentar os arquivos alterados;
19. o Codex apresentar os comandos executados e seus resultados;
20. um commit Git for criado ao final.

---

## 20. Definition of Done

A demanda somente pode ser considerada concluída quando:

* a causa real estiver confirmada por código, log ou reprodução;
* a correção estiver implementada;
* o início funcionar no PostgreSQL;
* a conclusão funcionar no PostgreSQL;
* os testes de regressão estiverem presentes;
* a suíte vigente estiver passando;
* `makemigrations --check --dry-run` não indicar alterações inesperadas;
* a documentação estiver atualizada;
* os cabeçalhos das cinco Specifications estiverem adicionados;
* a nova Specification estiver versionada;
* o diff final estiver revisado;
* não houver alteração fora do escopo;
* o commit Git estiver criado.

---

## 21. Instrução final para o Codex

Analise primeiro o estado atual do repositório e compare a implementação com:

```text
docs/specs/MIGRACAO_POSTGRES.md
docs/specs/AJUSTE_CONTRATO_INICIO_CATEGORIA_DEFAULT.md
docs/specs/FILA_RANDOMIZACAO_ATIVIDADES.md
docs/specs/ATIVIDADE_ATIVA_PERSISTENTE_MULTIPLATAFORMA.md
docs/specs/CONTADOR_ASSINCRONO_FRONTEND.md
```

Não assuma que a hipótese de timezone é a causa definitiva sem consultar o código e, quando possível, o traceback real.

Depois:

1. reproduza ou confirme a falha;
2. corrija o tratamento dos campos temporais;
3. preserve o fluxo da fila;
4. adicione testes;
5. valide em SQLite e PostgreSQL;
6. adicione os cabeçalhos YAML canônicos às cinco Specifications existentes;
7. crie `docs/specs/SPEC-BACK-006_CORRECAO_INICIO_EXECUCAO_TIMEZONE_POSTGRES.md`;
8. execute as validações;
9. revise o diff;
10. crie um commit Git.

Mensagem sugerida para o commit:

```text
fix(pomodoro): corrige inicio e conclusao de atividades no PostgreSQL
```

Ao final, apresentar:

* causa confirmada;
* arquivos alterados, com caminhos relativos;
* resumo das alterações;
* testes adicionados;
* comandos executados;
* resultados obtidos;
* validação realizada em PostgreSQL;
* eventuais divergências entre código e Specifications;
* hash e mensagem do commit;
* pendências que permanecerem fora do escopo.
