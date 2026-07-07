# Fila de randomizacao de atividades

## Status

- Tipo: spec/plano.
- Estado: pronto para validacao de produto antes da implementacao.
- Escopo: backend Django/DRF do app Pomodoro.
- Risco: medio-alto, pois altera a selecao de proxima atividade, cria persistencia nova e muda o significado de atividades feitas e puladas.

## Objetivo

Substituir a randomizacao sob demanda de `GET /api/activities/next/` por uma fila persistida. A primeira execucao deve gerar uma ordem aleatoria para todas as atividades elegiveis, respeitando grupos predefinidos. Depois disso, o usuario consome a fila: ao pular uma atividade, o backend registra em uma lista de puladas; ao fazer a atividade, registra em uma lista de favoritas.

A cada pool de 30 atividades feitas ou puladas, o backend gera a proxima fila aleatoria com peso maior para atividades favoritas. Depois de 5 execucoes dessa pool, a proxima etapa deve restringir a selecao as atividades puladas; na 6a pool, nao deve ser possivel pular para outras atividades.

## Diagnostico do fluxo atual

Hoje `ActivityViewSet.next` executa a selecao diretamente no banco:

- busca atividades ativas;
- exclui atividades que possuem `History.end_time` no dia atual;
- restringe por `group_id` ou `group_name`, quando informado;
- remove categorias que atingiram `max_daily_executions`;
- ordena por `-premium` e `Random()`;
- retorna apenas o primeiro item.

Esse desenho nao preserva a ordem sorteada entre chamadas. Cada refresh ou nova chamada pode retornar outra atividade elegivel, e nao existe estado de fila, pool, favorita ou pulada. A persistencia atual tambem diferencia apenas `Schedule` e `History`; uma atividade iniciada ja cria `History`, e a conclusao marca `Schedule.completed`.

## Decisoes de produto propostas

### Terminologia

Para evitar ambiguidade, a implementacao deve padronizar:

| Termo | Significado |
| --- | --- |
| Fila | Sequencia persistida de atividades que serao apresentadas ao usuario. |
| Item da fila | Uma atividade em uma posicao especifica da fila. |
| Pool ou lote | Janela de 30 itens consumidos, contando feitos e pulados. |
| Feita | Atividade iniciada e concluida com sucesso. |
| Pulada | Atividade recusada explicitamente pelo usuario antes de iniciar. |
| Favorita | Atividade feita dentro do mecanismo de fila, usada como sinal positivo para peso futuro. |

### Geracao inicial

Na primeira execucao sem fila ativa:

1. o backend seleciona todas as atividades elegiveis;
2. aplica filtros de grupo predefinido, atividade ativa, atividade premium vigente e limite diario por categoria;
3. embaralha a ordem;
4. persiste a fila;
5. retorna o primeiro item pendente.

O filtro de grupo deve continuar sendo uma restricao do universo, nao um contador separado. A regra atual de limite diario por categoria deve ser preservada ate que exista decisao explicita em contrario.

### Consumo da fila

O endpoint de proxima atividade deve retornar o primeiro item pendente da fila ativa. Ele nao deve sortear novamente enquanto houver item pendente valido.

Ao iniciar e completar uma atividade:

- o item da fila passa para `completed`;
- a atividade entra no historico positivo da fila;
- a atividade recebe ou reforca peso de favorita para pools futuras;
- `Schedule` e `History` continuam sendo a fonte do historico operacional de execucao.

Ao pular uma atividade:

- o item da fila passa para `skipped`;
- o backend grava o evento na tabela de puladas;
- a atividade nao deve aparecer novamente na mesma pool normal, exceto nas fases restritas a puladas;
- pular nao deve criar `Schedule` nem `History`.

## Regra de pools

Uma pool fecha quando a soma de itens `completed` e `skipped` atinge 30.

Ao fechar uma pool normal:

1. calcular favoritos e puladas acumulados;
2. gerar uma nova fila aleatoria;
3. aplicar peso maior para favoritas;
4. manter alguma chance de atividades nao favoritas para evitar repeticao excessiva;
5. respeitar filtros de atividade ativa, grupo e limites diarios.

### Peso das favoritas

Proposta inicial de pesos:

| Condicao da atividade | Peso |
| --- | ---: |
| Favorita recente | 4 |
| Favorita historica | 2 |
| Neutra | 1 |
| Pulada recente em pool normal | 1 |

Atividades premium vigentes continuam tendo prioridade antes da ponderacao ou recebem multiplicador adicional. A decisao recomendada e manter a garantia atual de premium primeiro, mas ordenar aleatoriamente dentro do conjunto premium com os mesmos pesos.

### Regra das 5 execucoes

A frase "depois de 5 execucao, dessa pull, somente poderar ser feitas as puladas" precisa virar uma regra verificavel. Interpretacao proposta:

- a cada 5 pools normais concluidas, o sistema entra em uma pool de revisao;
- a pool de revisao contem apenas atividades puladas em pools anteriores;
- nessa pool, atividades favoritas e neutras nao entram na fila;
- concluir uma pulada remove ou reduz sua penalidade de pulada;
- pular novamente mantem a atividade na lista de puladas.

### Sexta pool sem pular

Interpretacao proposta da frase "na sexta pull, sem pular para outras":

- a 6a pool e a pool de revisao de puladas;
- durante essa pool, o usuario nao pode usar o endpoint de pular;
- o usuario deve fazer a atividade apresentada ou encerrar/pausar a sessao;
- `POST skip` deve responder `409 Conflict` com codigo estavel quando a pool estiver travada.

Essa decisao precisa de confirmacao, porque tambem pode significar "na sexta atividade dentro da mesma pool" ou "na sexta tentativa de uma pulada". A implementacao nao deve seguir sem fechar essa semantica.

## Modelo de dados previsto

Criar modelos novos no app `pomodoro`, mantendo `Activity`, `Schedule` e `History` para o historico de execucao:

| Modelo | Campos principais |
| --- | --- |
| `ActivityQueue` | `id`, `group`, `state`, `mode`, `pool_number`, `pool_size`, `consumed_count`, `skip_locked`, `created_at`, `closed_at` |
| `ActivityQueueItem` | `queue`, `activity`, `position`, `state`, `presented_at`, `started_at`, `completed_at`, `skipped_at` |
| `ActivityPreferenceEvent` | `activity`, `event_type`, `queue`, `queue_item`, `weight_delta`, `created_at` |

Estados sugeridos para `ActivityQueue`:

- `active`;
- `closed`;
- `cancelled`.

Modos sugeridos:

- `normal`;
- `skipped_review`.

Estados sugeridos para `ActivityQueueItem`:

- `pending`;
- `presented`;
- `started`;
- `completed`;
- `skipped`;
- `expired`.

`ActivityPreferenceEvent.event_type` deve aceitar pelo menos:

- `favorite_completed`;
- `skipped`;
- `skipped_completed`;

Se o produto exigir tabelas fisicas separadas para favoritas e puladas, elas podem ser materializadas como `FavoriteActivity` e `SkippedActivity`. A recomendacao tecnica e registrar eventos imutaveis e derivar o saldo atual, porque isso preserva auditoria e permite ajustar pesos sem perder historico.

## Servico de dominio

Adicionar uma camada de servico para tirar regra de negocio de `views.py`:

| Arquivo | Responsabilidade |
| --- | --- |
| `apps/pomodoro/services/activity_queue.py` | Gerar filas, calcular pesos, consumir itens, fechar pools e abrir revisoes de puladas. |
| `apps/pomodoro/selectors/activity_eligibility.py` | Centralizar filtros de atividades elegiveis, grupo, premium e limite diario. |
| `apps/pomodoro/repositories/activity_queue.py` | Encapsular queries transacionais se a complexidade justificar. |

A view deve coordenar request, permissao e response. A regra de fila, pesos e transicao de estado deve ficar no servico.

## Contrato HTTP esperado

### Obter proxima atividade

`GET /api/activities/next/?group_id=<id>`

Resposta `200 OK`:

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

Sem atividade disponivel:

- `404 Not Found` quando nao existe atividade elegivel;
- `204 No Content` pode ser adotado quando a fila existe, mas nao ha item disponivel no momento.

A escolha entre `404` e `204` deve ser fechada antes da implementacao para evitar contrato ambiguo no frontend.

### Pular atividade

`POST /api/activity-queue/items/{queue_item_id}/skip/`

Resposta `200 OK`:

```json
{
  "queue_item_id": 91,
  "activity_id": 7,
  "state": "skipped",
  "next_queue_item_id": 92
}
```

Quando a pool estiver travada:

```json
{
  "code": "skip_locked",
  "detail": "Esta pool permite apenas executar atividades puladas."
}
```

Status: `409 Conflict`.

### Iniciar e completar

O `POST /api/activities/{id}/start/` deve receber opcionalmente `queue_item_id`. Ao completar a execucao, o backend deve marcar o item da fila como `completed` dentro de uma transacao.

Exemplo de inicio:

```json
{
  "queue_item_id": 91,
  "group_id": 1
}
```

Se o cliente tentar iniciar uma atividade diferente do item atual da fila, o backend deve responder `409 Conflict` com codigo `queue_item_mismatch`.

## Concorrencia e consistencia

- A fila ativa deve ser unica por escopo. Enquanto nao existir usuario autenticado, o escopo minimo deve ser a API key ou uma instalacao explicitamente single-tenant.
- Geracao de fila deve ocorrer em transacao e bloquear a fila ativa do escopo para evitar duas filas simultaneas.
- `queue_item_id` deve ser obrigatorio para marcar uma atividade como feita ou pulada dentro do novo fluxo.
- Conclusao repetida deve ser idempotente: um item ja `completed` continua `completed` e nao duplica evento favorito.
- Pulo repetido deve ser idempotente enquanto o item estiver `skipped` e nao pode criar eventos duplicados.
- Atividades desativadas depois da geracao da fila devem ser ignoradas no momento de apresentacao e marcadas como `expired`.

## Compatibilidade com regras atuais

Devem continuar valendo:

- apenas atividades `active=True` entram na apresentacao;
- premium expirado e desativado automaticamente antes da selecao;
- grupo informado restringe o universo da fila;
- limite diario por categoria bloqueia apresentacao/inicio quando atingido;
- atividade ja concluida no dia nao deve voltar em pool normal;
- `Schedule.unique_together(activity, scheduled_date)` impede repetir a mesma atividade no mesmo dia no modelo atual.

O ultimo ponto e um bloqueio importante para a regra de revisao de puladas se ela puder apresentar uma atividade ja feita no mesmo dia. A implementacao deve decidir se revisao de puladas respeita o bloqueio diario atual ou se uma migracao futura remodelara `Schedule`.

## Plano de implementacao

1. Confirmar a semantica da 6a pool e o escopo da fila.
2. Criar services/selectors para reproduzir a elegibilidade atual com testes antes de mudar o endpoint.
3. Criar migrations dos modelos de fila e eventos.
4. Implementar geracao inicial de fila por grupo/escopo.
5. Alterar `GET /api/activities/next/` para consumir fila persistida.
6. Criar endpoint de pular e registrar evento `skipped`.
7. Integrar `start`/`complete` com `queue_item_id` e registrar evento positivo.
8. Implementar fechamento de pool com 30 consumos e geracao ponderada da proxima fila.
9. Implementar pool de revisao de puladas a cada 5 pools normais.
10. Ajustar frontend para consumir `queue_id`, `queue_item_id`, `skip_locked` e erros estaveis.

## Testes esperados

### Backend

- primeira chamada gera fila persistida e retorna o primeiro item;
- chamadas repetidas retornam o mesmo item enquanto ele estiver pendente/apresentado;
- pular marca item como `skipped`, cria evento de pulada e retorna proximo item;
- completar marca item como `completed`, cria evento favorito e nao duplica em chamada repetida;
- apos 30 itens consumidos, uma nova fila e criada;
- favoritas aparecem com frequencia maior em amostra deterministica usando seed controlada;
- apos 5 pools normais, a proxima pool contem apenas puladas;
- na pool travada, `POST skip` retorna `409 skip_locked`;
- atividade inativa nao aparece mesmo se estava na fila;
- limite diario por categoria continua bloqueando apresentacao ou inicio;
- grupos diferentes nao misturam filas quando o escopo exigir separacao.

### Frontend

- botao de pular chama o novo endpoint e atualiza para a proxima atividade;
- botao de pular fica indisponivel quando `skip_locked=true`;
- start envia `queue_item_id`;
- erro `queue_item_mismatch` força refresh da proxima atividade;
- tela exibe corretamente pool normal e pool de revisao, se houver indicador de produto.

## Validacoes manuais

1. Criar ao menos 35 atividades ativas em grupos diferentes.
2. Abrir a tela e confirmar que a ordem nao muda ao atualizar sem consumir item.
3. Pular algumas atividades e concluir outras ate fechar 30 consumos.
4. Confirmar que a pool seguinte favorece atividades feitas.
5. Repetir ate a 6a pool e confirmar que apenas puladas aparecem.
6. Confirmar que nao e possivel pular durante a pool travada.

## Riscos

| Risco | Mitigacao |
| --- | --- |
| Ambiguidade em "5 execucoes" e "sexta pull" | Fechar decisao de produto antes de codar a regra. |
| Duas filas ativas por concorrencia | Constraint por escopo e transacao com lock. |
| Pesos gerarem repeticao excessiva | Definir teto de repeticao e manter chance minima para neutras. |
| `Schedule` atual impedir repeticao diaria | Decidir se a revisao de puladas respeita ou altera esse contrato. |
| API sem usuario autenticado misturar consumidores | Escopar por API key ou declarar instalacao single-tenant. |
| Randomizacao dificil de testar | Injetar seed/gerador no service em testes unitarios. |

## Fora de escopo

- Reescrever todo o modelo temporal de `Schedule`.
- Criar recomendacao por machine learning.
- Sincronizacao em tempo real por WebSocket/SSE.
- Alterar autenticacao da API.
- Migrar para outro framework ou trocar banco.

## Decisoes pendentes

1. Confirmar se "pull" significa pool/lote de 30 atividades.
2. Confirmar se a 6a pool e uma pool de revisao sem pulo ou se a regra se refere a sexta atividade/tentativa.
3. Definir se favoritas e puladas precisam ser tabelas materializadas ou se eventos imutaveis atendem o produto.
4. Definir escopo da fila: global, por API key, por grupo ou futuro usuario.
5. Definir se a pool de revisao de puladas pode apresentar atividade ja feita no mesmo dia.
6. Definir o comportamento sem item disponivel: `404` ou `204`.
