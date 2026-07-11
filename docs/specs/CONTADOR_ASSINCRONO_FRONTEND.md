---

spec_id: SPEC-BACK-005
titulo: Contador persistente e sincronização do frontend
status: APPROVED
fase: AS_IS
situacao: VIGENTE
responsavel: Arquitetura de Software
criado_em: 2026-06-23
atualizado_em: 2026-07-11
documento_principal:

* SPEC-BACK-005
  dependencias:
* SPEC-BACK-001
* SPEC-BACK-002
* SPEC-BACK-003
* SPEC-BACK-004
  substitui: []
  substituida_por: []

---

# SPEC-BACK-005 — Contador persistente e sincronização do frontend

## 1. Objetivo

Definir o contrato canônico de persistência temporal e sincronização entre o backend Pomodoro e seus clientes.

A execução iniciada deve continuar válida quando:

* o aplicativo for suspenso;
* o aplicativo for encerrado;
* o dispositivo perder conectividade;
* o aplicativo for aberto novamente;
* outro cliente autorizado consultar a execução;
* o contador visual local parar de atualizar;
* o processo responsável pela conclusão automática sofrer atraso.

O backend é a fonte de verdade do estado da execução.

O frontend deve:

* projetar visualmente o tempo restante;
* consultar o estado persistido;
* reconciliar divergências;
* não concluir automaticamente a atividade apenas porque o contador local chegou a zero;
* não depender exclusivamente de armazenamento local para descobrir uma execução ativa.

---

## 2. Estado da Specification

### Classificação geral

**PARCIALMENTE CONFIRMADO**

Está confirmado nos fontes atuais que:

* `Schedule` possui campos temporais completos;
* existem estados explícitos de execução;
* existe controle de versão;
* existe `scope_key`;
* existe constraint para limitar execução aberta por escopo;
* o início da atividade persiste a execução;
* a execução pode ser consultada por endpoint;
* existe endpoint de execução ativa;
* existe reconciliação de execução;
* o tempo restante é calculado a partir de prazos persistidos;
* o backend retorna dados suficientes para sincronização;
* o início utiliza `queue_item_id`;
* a conclusão é integrada ao item da fila;
* existem serviços específicos para execução e fila.

Permanecem parcialmente confirmados ou não localizados:

* scheduler APScheduler em processo dedicado;
* serviço `scheduler` no Compose;
* job persistente por execução;
* reconciliação periódica independente de requisição HTTP;
* conclusão automática com todos os clientes fechados;
* implementação integral do modelo tipado no Flutter;
* remoção da conclusão automática disparada pelo contador local;
* polling de 30 segundos no frontend;
* compartilhamento integral da mesma lógica entre desktop e mobile;
* homologação em mais de um dispositivo.

---

## 3. Contexto

O frontend já utilizava prazo absoluto para projetar o tempo restante, evitando depender somente da quantidade de ticks locais.

Porém, o estado operacional ainda possuía dependências do cliente:

* preparação iniciada localmente;
* execução conhecida apenas por armazenamento local;
* conclusão enviada pelo frontend;
* recuperação condicionada à existência de snapshot local;
* tratamento de falhas disperso;
* lógica duplicada entre mobile e desktop;
* parsing de payloads diretamente na interface;
* ausência de descoberta de execução ativa sem ID conhecido.

A evolução definida nesta Specification transfere a autoridade temporal para o backend.

---

## 4. Princípio de autoridade temporal

O backend é a autoridade para:

* registrar a solicitação de início;
* definir o início efetivo;
* definir o prazo esperado;
* controlar o estado;
* concluir a execução;
* registrar o histórico;
* determinar se ainda existe execução ativa;
* retornar o tempo restante;
* resolver conflitos;
* reconciliar execuções vencidas.

O frontend é responsável por:

* apresentar o contador;
* atualizar a tela localmente;
* sincronizar periodicamente;
* reagir às respostas do backend;
* exibir estados de carregamento, conflito e indisponibilidade;
* agendar notificações locais como melhor esforço.

O frontend não deve ser a autoridade para alterar automaticamente o estado persistido para concluído.

---

## 5. Escopo

Esta Specification contempla:

* persistência dos prazos da execução;
* estados temporais;
* controle de versão;
* escopo da execução;
* execução ativa;
* contrato de início;
* contrato de consulta;
* contrato de execução ativa;
* reconciliação;
* contador visual;
* sincronização de relógio;
* armazenamento local;
* polling;
* lifecycle do aplicativo;
* comportamento offline;
* conclusão automática no backend;
* scheduler;
* integração mobile e desktop;
* notificações locais;
* rollout compatível.

---

## 6. Fora de escopo

Não fazem parte desta Specification:

* WebSocket;
* Server-Sent Events;
* FCM;
* APNs;
* Web Push;
* pausa e retomada;
* múltiplas execuções simultâneas;
* autenticação completa de usuários;
* recomendação por machine learning;
* alteração da duração após o início;
* alteração da atividade após o início;
* reformulação da fila persistida;
* alteração dos pesos;
* criação de um sistema distribuído de jobs;
* garantia de notificação push em aparelhos fechados;
* correção específica dos campos `TimeField` no PostgreSQL.

A correção dos campos temporais de início e conclusão pertence à:

```text
SPEC-BACK-006
```

---

## 7. Relação com outras Specifications

| Specification   | Relação                                                  |
| --------------- | -------------------------------------------------------- |
| `SPEC-BACK-001` | Define PostgreSQL, timezone e ambiente operacional.      |
| `SPEC-BACK-002` | Define início idempotente e proibição de retorno `304`.  |
| `SPEC-BACK-003` | Define fila persistida e integração por `queue_item_id`. |
| `SPEC-BACK-004` | Define a execução ativa persistente e seus estados.      |
| `SPEC-BACK-006` | Corrige a regressão temporal no início e na conclusão.   |

Esta Specification não substitui a `SPEC-BACK-004`.

A `SPEC-BACK-004` é a referência do domínio de execução ativa.

A `SPEC-BACK-005` é a referência da projeção temporal e sincronização do frontend com essa execução.

---

## 8. Modelo temporal

A execução deve possuir timestamps completos com timezone.

Campos identificados ou esperados:

```text
requested_at
starts_at
expected_end_at
completed_at
```

Também deve possuir:

```text
state
version
scope_key
```

Campos `DateTimeField` devem permanecer timezone-aware.

Campos legados de data e hora podem ser mantidos por compatibilidade, mas não devem ser usados como única fonte para reconstruir o instante real.

---

## 9. Estados da execução

Estados identificados no domínio:

```text
preparing
running
completed
cancelled
expired
```

Nem todos necessariamente são produzidos por todos os fluxos atuais.

### 9.1 `preparing`

A solicitação foi aceita, mas a atividade ainda não entrou na fase efetiva.

Condição temporal:

```text
server_now < starts_at
```

### 9.2 `running`

A atividade está em execução.

Condição temporal:

```text
starts_at <= server_now < expected_end_at
```

### 9.3 `completed`

A execução foi finalizada e persistida.

### 9.4 `cancelled`

A execução foi interrompida por operação explícita.

O fluxo completo de cancelamento não foi confirmado nesta revisão.

### 9.5 `expired`

A execução ou item deixou de ser válido.

O uso efetivo deste estado deve ser comprovado pelo serviço vigente.

---

## 10. Preparação

A preparação deve fazer parte da execução persistida.

Ao clicar em `Start`, o frontend deve enviar imediatamente a requisição ao backend.

O backend define:

```text
requested_at
starts_at
preparation_seconds
```

Quando não houver preparação:

```text
preparation_seconds = 0
starts_at = requested_at
```

O frontend não deve criar uma preparação exclusivamente em memória antes de comunicar o backend.

---

## 11. Escopo da execução

A aplicação é pessoal e atualmente possui somente um usuário.

Mesmo assim, a execução aberta é identificada por um `scope_key`.

A finalidade é:

* impedir duas execuções abertas no mesmo contexto;
* permitir recuperação;
* associar fila e execução;
* preservar idempotência;
* permitir futura evolução sem remodelagem imediata.

A existência de autenticação de aplicação por API Key não equivale a um domínio completo de usuários.

O escopo atual deve ser entendido como identidade técnica da instalação ou credencial.

---

## 12. Constraint de execução aberta

Deve existir no máximo uma execução aberta por escopo.

A constraint deve ser equivalente a:

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

A implementação também deve:

* usar transação;
* bloquear registros relevantes;
* tratar `IntegrityError`;
* retornar a execução existente ou conflito estável;
* não depender apenas de consulta anterior à criação.

---

## 13. Contrato de início

## 13.1 Endpoint

```http
POST /api/activities/{activity_id}/start/
```

No fluxo vigente da fila, o payload inclui:

```json
{
  "queue_item_id": 91
}
```

O grupo não deve ser usado como substituto do item da fila.

---

## 13.2 Nova execução

Quando uma nova execução for criada:

```http
201 Created
```

Exemplo conceitual:

```json
{
  "execution_id": 42,
  "queue_id": 10,
  "queue_item_id": 91,
  "state": "preparing",
  "activity": {
    "id": 7,
    "name": "Estudar Python",
    "duration": 25
  },
  "requested_at": "2026-07-11T13:00:00Z",
  "starts_at": "2026-07-11T13:02:00Z",
  "expected_end_at": "2026-07-11T13:27:00Z",
  "completed_at": null,
  "preparation_seconds": 120,
  "remaining_seconds": 1620,
  "server_now": "2026-07-11T13:00:00Z",
  "version": 1,
  "status": "started"
}
```

O nome efetivo do identificador pode ser:

```text
execution_id
schedule_id
```

O serializer vigente deve ser tratado como referência para compatibilidade.

A documentação pública deve adotar um nome único.

---

## 13.3 Repetição idempotente

Quando a mesma execução já estiver aberta e for compatível:

```http
200 OK
```

A resposta deve:

* retornar a execução existente;
* preservar os horários originais;
* não criar novo histórico;
* não criar novo job;
* não consumir novo item;
* não alterar a versão sem necessidade;
* não retornar `304`.

---

## 13.4 Conflito

Quando existir outra atividade ativa incompatível:

```http
409 Conflict
```

Exemplo:

```json
{
  "code": "active_schedule_conflict",
  "detail": "Já existe uma atividade em execução.",
  "active_schedule": {
    "execution_id": 42,
    "state": "running"
  }
}
```

O frontend deve reagir ao campo `code`, não ao texto de `detail`.

---

## 14. Execução ativa

## 14.1 Endpoint

```http
GET /api/activities/active/
```

## 14.2 Com execução ativa

```http
200 OK
```

Deve retornar o schema temporal completo.

## 14.3 Sem execução ativa

O contrato originalmente definido é:

```http
204 No Content
```

O comportamento efetivamente implementado deve ser confirmado nos fontes e testes.

Se o backend atual retornar outro status estável, a documentação e o frontend devem ser alinhados.

---

## 15. Consulta de execução

Endpoint previsto:

```http
GET /api/activities/status/{execution_id}/
```

ou rota equivalente implementada no backend.

A resposta deve utilizar o mesmo serializer da execução ativa e do início sempre que possível.

Isso evita divergências entre:

* início;
* status;
* execução ativa;
* reconciliação.

Campos legados podem ser mantidos durante rollout, mas o contrato canônico utiliza:

```text
state
requested_at
starts_at
expected_end_at
completed_at
remaining_seconds
server_now
version
```

---

## 16. Reconciliação

A reconciliação verifica se o estado persistido ainda corresponde aos prazos.

Quando uma execução está aberta e:

```text
server_now >= expected_end_at
```

o backend deve concluir a execução de forma idempotente.

A reconciliação pode ocorrer por:

* job agendado;
* endpoint de execução ativa;
* endpoint de status;
* endpoint específico;
* rotina periódica.

Existe no backend rota ou serviço de reconciliação da execução.

A operação deve:

1. bloquear o `Schedule`;
2. verificar o estado atual;
3. calcular vencimento;
4. concluir somente se necessário;
5. atualizar o item da fila;
6. atualizar o histórico;
7. criar eventos de preferência;
8. impedir duplicidade;
9. retornar o estado final.

---

## 17. Conclusão automática

A conclusão automática deve ocorrer no backend.

O frontend não deve enviar uma conclusão automática somente porque seu contador visual chegou a zero.

Quando o contador chegar a:

```text
00:00
```

o frontend deve:

1. congelar a projeção;
2. entrar em estado de reconciliação;
3. consultar o backend;
4. aguardar `completed`;
5. atualizar histórico;
6. buscar a próxima atividade.

Caso o backend ainda retorne `running`, o frontend deve seguir a resposta do servidor.

---

## 18. Scheduler

## 18.1 Arquitetura esperada

O scheduler deve rodar em processo dedicado.

Não deve iniciar:

* em `AppConfig.ready()`;
* dentro de cada worker Gunicorn;
* em import de módulo;
* em middleware;
* por requisição HTTP.

O serviço deve utilizar:

* job store persistente;
* ID determinístico;
* conclusão transacional;
* tolerância a atraso;
* recuperação após restart;
* reconciliação periódica.

---

## 18.2 Job por execução

ID sugerido:

```text
complete-schedule-{schedule_id}
```

O agendamento deve ocorrer somente após commit:

```python
transaction.on_commit(...)
```

O job deve ser idempotente.

Execuções repetidas não podem:

* criar dois históricos;
* concluir duas vezes;
* duplicar evento positivo;
* incrementar contadores novamente;
* alterar `completed_at` indevidamente.

---

## 18.3 Estado identificado

**NÃO LOCALIZADO**

Na análise do backend não foi confirmada a presença de:

* processo dedicado de scheduler;
* serviço `scheduler` no Compose;
* criação de job por execução;
* job store PostgreSQL configurado;
* rotina periódica ativa independente de requisições.

A existência da dependência APScheduler não comprova que o worker esteja operacional.

Portanto, a conclusão automática com todos os clientes fechados permanece pendente de evidência.

---

## 19. Cálculo do tempo restante

O backend não deve persistir nem decrementar um contador a cada segundo.

O tempo restante é derivado:

```text
remaining_seconds = expected_end_at - server_now
```

O valor deve ser limitado a zero quando o prazo tiver expirado.

A resposta deve incluir:

```text
server_now
expected_end_at
remaining_seconds
```

O frontend pode atualizar visualmente uma vez por segundo sem requisição remota.

---

## 20. Sincronização de relógio

O frontend deve estimar o relógio do servidor a partir de:

```text
server_now
```

Estratégia recomendada:

```text
estimated_server_now =
    server_now + tempo_monotônico_decorrido
```

A fonte monotônica evita alterações causadas por:

* ajuste manual do relógio;
* sincronização automática do sistema;
* mudança de timezone;
* variações de `DateTime.now()`.

Quando houver nova resposta do backend, o cliente deve corrigir sua projeção.

A autoridade final permanece com o servidor.

---

## 21. Polling

Enquanto o contador estiver visível, o frontend pode consultar o backend periodicamente.

Intervalo de referência:

```text
30 segundos
```

O polling deve:

* iniciar somente quando necessário;
* ser cancelado no `dispose`;
* não criar timers duplicados;
* ser reiniciado após lifecycle;
* não controlar a conclusão;
* servir apenas para reconciliação visual.

Eventos que exigem sincronização imediata:

* inicialização;
* retorno do background;
* start;
* erro;
* contador chegando a zero;
* refresh manual;
* conectividade restaurada.

---

## 22. Estado do frontend

O frontend deve utilizar modelo tipado.

Modelo sugerido:

```text
ActivityExecution
```

Estados de apresentação:

| Estado                | Comportamento                           |
| --------------------- | --------------------------------------- |
| `initialLoading`      | Consulta grupos e execução ativa.       |
| `idle`                | Não há execução ativa.                  |
| `starting`            | Requisição de início em andamento.      |
| `preparing`           | Exibe preparação até `starts_at`.       |
| `running`             | Exibe tempo até `expected_end_at`.      |
| `reconciling`         | Consulta o estado real.                 |
| `completed`           | Atualiza histórico e próxima atividade. |
| `offlineWithSnapshot` | Exibe cache sem autoridade.             |
| `error`               | Exibe falha recuperável.                |

---

## 23. Transições do frontend

```text
bootstrap
  → GET active
  → idle | preparing | running
```

```text
idle
  → start
  → starting
  → preparing | running
```

```text
preparing
  → starts_at alcançado
  → running
```

```text
running
  → expected_end_at alcançado
  → reconciling
  → completed
```

```text
background/resume
  → reconciliar
  → estado retornado pelo backend
```

```text
falha de rede
  → offlineWithSnapshot
  → reconciling
```

---

## 24. Armazenamento local

`SharedPreferences` não deve ser fonte de verdade.

O armazenamento local pode ser:

* removido; ou
* mantido como cache de snapshot.

Um snapshot local:

* não autoriza conclusão;
* não autoriza início;
* não autoriza troca de atividade;
* não substitui `GET active`;
* deve ser sobrescrito após sincronização;
* deve ser marcado como potencialmente desatualizado.

Campos mínimos sugeridos:

```text
execution_id
activity_id
state
starts_at
expected_end_at
version
updated_at
```

---

## 25. Notificações locais

Notificação local é melhor esforço.

O backend não consegue garantir notificação no dispositivo apenas concluindo o `Schedule`.

Para notificação confiável com aplicativo encerrado seriam necessários:

* token de dispositivo;
* serviço push;
* FCM, APNs ou Web Push.

Isso permanece fora do escopo.

O frontend deve evitar notificações duplicadas usando o identificador da execução como chave.

---

## 26. Integração com a fila

A execução ativa está associada a um item da fila.

Ao iniciar:

```text
presented → started
```

Ao concluir:

```text
started → completed
```

A sincronização deve preservar:

```text
queue_id
queue_item_id
```

Quando a execução for concluída automaticamente:

* o item deve ser concluído;
* o histórico operacional deve ser finalizado;
* o evento de preferência deve ser criado;
* o contador da fila deve ser atualizado;
* a pool deve ser avaliada.

A conclusão não pode atualizar somente `Schedule`.

---

## 27. Compatibilidade durante rollout

O backend deve ser publicado antes do frontend incompatível.

Durante a transição, pode ser necessário manter:

```text
schedule_id
execution_id
expected_end_time
expected_end_at
is_completed
state
```

A duplicidade temporária de campos deve possuir prazo de remoção.

O frontend novo deve consumir o contrato canônico.

Campos antigos somente devem ser removidos após:

* atualização dos clientes;
* homologação;
* confirmação de inexistência de consumidores legados.

---

## 28. Tratamento de erros

Erros devem possuir formato estável:

```json
{
  "code": "active_schedule_conflict",
  "detail": "Já existe uma atividade em execução."
}
```

O frontend deve decidir pelo:

* status HTTP;
* campo `code`;
* presença de execução ativa.

Não deve fazer parsing da mensagem textual.

Falhas de sincronização não devem ser descartadas silenciosamente.

---

## 29. Observabilidade

O backend deve registrar:

* criação da execução;
* reutilização idempotente;
* conflito;
* agendamento do job;
* execução do job;
* reconciliação;
* conclusão;
* falha;
* duração do atraso;
* versão;
* origem da transição.

Não deve registrar:

* API Key;
* `Authorization`;
* tokens;
* dados sensíveis;
* payload completo sem sanitização.

---

## 30. Arquivos relacionados

### Backend

| Caminho relativo                               | Responsabilidade                   |
| ---------------------------------------------- | ---------------------------------- |
| `apps/pomodoro/models.py`                      | Persistência e estados.            |
| `apps/pomodoro/services/activity_execution.py` | Início, conclusão e reconciliação. |
| `apps/pomodoro/services/activity_queue.py`     | Integração com item e pool.        |
| `apps/pomodoro/views.py`                       | Endpoints HTTP.                    |
| `apps/pomodoro/serializers.py`                 | Contrato da execução.              |
| `apps/pomodoro/urls.py`                        | Rotas.                             |
| `apps/pomodoro/tests.py`                       | Testes.                            |
| `compose.yml`                                  | Serviços da aplicação.             |

### Frontend previsto

| Caminho relativo                            | Responsabilidade          |
| ------------------------------------------- | ------------------------- |
| `lib/models/activity_execution.dart`        | Modelo tipado.            |
| `lib/services/api_service.dart`             | Contratos HTTP.           |
| `lib/services/active_activity_storage.dart` | Cache local.              |
| `lib/controllers/pomodoro_controller.dart`  | Estado e sincronização.   |
| `lib/widgets/countdown_timer.dart`          | Apresentação do contador. |
| `lib/screens/home_mobile.dart`              | Interface mobile.         |
| `lib/screens/home_desktop.dart`             | Interface desktop.        |
| `lib/services/notification_service.dart`    | Notificações locais.      |

Os caminhos do frontend não foram comprovados no ZIP do backend e devem ser confirmados no repositório Flutter.

---

## 31. Testes obrigatórios do backend

* início persiste os prazos;
* repetição não duplica execução;
* outra atividade retorna conflito;
* execução ativa pode ser descoberta;
* status retorna o mesmo contrato;
* `remaining_seconds` é calculado corretamente;
* `server_now` é retornado;
* versão é preservada;
* execução vencida é reconciliada;
* conclusão repetida é idempotente;
* item da fila é concluído;
* evento positivo não é duplicado;
* concorrência não cria duas execuções;
* timezone funciona no PostgreSQL;
* campos `TimeField` são compatíveis após `SPEC-BACK-006`.

---

## 32. Testes obrigatórios do scheduler

Quando o scheduler for implementado:

* job é criado após commit;
* job possui ID determinístico;
* restart não perde a execução;
* job atrasado conclui dentro da tolerância;
* job duplicado não duplica dados;
* job e reconciliação concorrentes resultam em uma conclusão;
* job conclui com clientes fechados;
* reconciliação periódica encontra jobs perdidos;
* falha temporária pode ser recuperada;
* scheduler não inicia nos workers web.

---

## 33. Testes obrigatórios do frontend

* start chama o backend imediatamente;
* clique repetido é bloqueado;
* preparação usa prazo do backend;
* app reabre sem cache e recupera execução;
* background não altera o prazo;
* relógio local incorreto não muda a regra;
* zero local não chama conclusão;
* `204` leva ao estado ocioso;
* conflito recupera execução ativa;
* falha de rede usa snapshot sem autoridade;
* polling não duplica timer;
* mobile e desktop apresentam os mesmos estados;
* parsing é tipado;
* `304` não é tratado como sucesso.

---

## 34. Validações técnicas

### Backend

```bash
APP_ENV=test poetry run python manage.py check \
  --settings=config.settings.test
```

```bash
APP_ENV=test poetry run python manage.py \
  makemigrations --check --dry-run \
  --settings=config.settings.test
```

```bash
APP_ENV=test poetry run python manage.py test \
  --settings=config.settings.test
```

Também deve haver validação em PostgreSQL.

### Frontend

```bash
dart format --output=none --set-exit-if-changed lib test
```

```bash
flutter analyze
```

```bash
flutter test
```

---

## 35. Validação manual mínima

1. obter item da fila;
2. iniciar no dispositivo A;
3. confirmar persistência imediata;
4. abrir dispositivo B sem cache;
5. confirmar mesma execução;
6. fechar os clientes;
7. aguardar o prazo;
8. confirmar conclusão no backend;
9. reabrir o aplicativo;
10. confirmar ausência de execução ativa;
11. confirmar histórico;
12. confirmar item da fila concluído;
13. confirmar próxima atividade.

Sem scheduler operacional, os passos 7 e 8 dependem de reconciliação posterior e não comprovam conclusão automática com clientes fechados.

---

## 36. Critérios de aceite

A Specification será considerada atendida quando:

1. start ocorrer no clique;
2. execução for persistida imediatamente;
3. frontend descobrir execução sem cache;
4. contador usar prazos absolutos;
5. backend for autoridade temporal;
6. frontend não concluir automaticamente no zero;
7. execução vencida for reconciliada;
8. execução ativa possuir escopo;
9. somente uma execução aberta existir por escopo;
10. respostas incluírem estado e versão;
11. fila e execução permanecerem consistentes;
12. mobile e desktop utilizarem contrato compatível;
13. falhas forem tratadas;
14. testes passarem;
15. o fluxo funcionar em PostgreSQL;
16. a regressão da `SPEC-BACK-006` estiver corrigida.

Para considerar também a conclusão totalmente independente dos clientes:

17. scheduler dedicado deve estar operacional;
18. job deve ser persistente;
19. restart deve ser suportado;
20. execução deve concluir com todos os clientes fechados.

---

## 37. Definition of Done

A Specification somente estará integralmente concluída quando:

* schema temporal estiver versionado;
* início persistente estiver implementado;
* execução ativa estiver implementada;
* status unificado estiver implementado;
* reconciliação estiver implementada;
* fila estiver integrada;
* frontend estiver tipado;
* contador for somente visual;
* armazenamento local for somente cache;
* polling estiver controlado;
* scheduler dedicado estiver implementado;
* jobs estiverem persistidos;
* conclusão com clientes fechados estiver validada;
* testes backend e frontend passarem;
* PostgreSQL estiver validado;
* documentação estiver atualizada;
* rollout estiver concluído.

---

## 38. Pendências

Permanecem pendentes:

* confirmar scheduler dedicado;
* confirmar serviço de scheduler no Compose;
* implementar ou comprovar job por execução;
* comprovar conclusão sem requisição HTTP;
* comprovar reconciliação periódica;
* validar restart;
* validar execução em dois dispositivos;
* analisar o repositório Flutter;
* confirmar modelo tipado;
* confirmar remoção da conclusão local;
* confirmar polling;
* confirmar uso de relógio monotônico;
* confirmar armazenamento como cache;
* confirmar compartilhamento entre mobile e desktop;
* corrigir o início conforme `SPEC-BACK-006`;
* homologar o fluxo completo.

---

## 39. Divergências corrigidas nesta revisão

| Informação anterior                                    | Estado atualizado                                |
| ------------------------------------------------------ | ------------------------------------------------ |
| Documento estava pronto para implementação             | Parte relevante do backend já está implementada. |
| Não existia endpoint de execução ativa                 | Endpoint foi identificado no backend.            |
| Não existia escopo de execução                         | `scope_key` foi implementado.                    |
| Não existia controle de versão                         | `version` foi identificado.                      |
| Não existiam estados persistidos                       | Estados existem no domínio.                      |
| Reconciliação era somente proposta                     | Serviço ou endpoint de reconciliação existe.     |
| Início não estava associado à fila                     | O início atual usa `queue_item_id`.              |
| Scheduler foi tratado como pré-requisito implementável | Continua não comprovado no estado atual.         |
| Frontend foi descrito como parte da implementação      | O frontend não foi fornecido nesta análise.      |
| Documento era integralmente TO-BE                      | Atualizado para AS-IS parcial.                   |

---

## 40. Histórico

| Data       | Versão | Alteração                                                                                                                          | Responsável             |
| ---------- | -----: | ---------------------------------------------------------------------------------------------------------------------------------- | ----------------------- |
| 2026-06-23 |    1.0 | Criação da proposta de contador persistente, autoridade temporal do backend e sincronização multiplataforma.                       | Arquitetura de Software |
| 2026-06-23 |    1.1 | Inclusão de scheduler dedicado, reconciliação, polling, modelo tipado e rollout backend-first.                                     | Arquitetura de Software |
| 2026-07-11 |    2.0 | Inclusão do identificador canônico `SPEC-BACK-005` e atualização do documento para AS-IS parcial com base no backend implementado. | Arquitetura de Software |
| 2026-07-11 |    2.1 | Registro da execução ativa, controle de versão, escopo, reconciliação e integração com fila como itens confirmados.                | Arquitetura de Software |
| 2026-07-11 |    2.2 | Registro do scheduler dedicado e da adequação Flutter como pendências não comprovadas.                                             | Arquitetura de Software |
| 2026-07-11 |    2.3 | Inclusão da dependência da `SPEC-BACK-006` para correção temporal no PostgreSQL.                                                   | Arquitetura de Software |

---

## 41. Conclusão

O backend já possui parte importante da arquitetura necessária para contador persistente e sincronização:

* execução persistida;
* prazos absolutos;
* estado;
* versão;
* escopo;
* execução ativa;
* consulta;
* reconciliação;
* integração com fila.

A autoridade temporal está parcialmente transferida ao backend.

Entretanto, não há evidência suficiente de que a conclusão automática esteja totalmente independente de requisições dos clientes, pois o scheduler dedicado e seus jobs não foram confirmados.

A integração Flutter também precisa ser analisada separadamente para comprovar:

* modelo tipado;
* contador somente visual;
* remoção da conclusão automática local;
* recuperação sem cache;
* polling;
* tratamento offline;
* compartilhamento entre mobile e desktop.

A Specification permanece vigente como referência da sincronização temporal entre backend e frontend.
