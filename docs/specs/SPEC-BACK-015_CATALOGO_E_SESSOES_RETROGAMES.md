---
spec_id: SPEC-BACK-015
titulo: Catálogo, progresso e sessões diretas de RetroGames
status: PLANNED
fase: TO_BE
criado_em: 2026-09-24
dependencias:
  - SPEC-014
---

# SPEC-BACK-015 — Catálogo, progresso e sessões diretas de RetroGames

## 1. Objetivo

Criar no backend um catálogo administrável de jogos retrô organizado como:

```text
Grupo Retrogames
└── Categoria/Geração
    └── Plataforma
        └── Jogo/Activity
```

O catálogo alimentará uma área **RetroGames** no Flutter. Nela, o usuário poderá
filtrar gerações e plataformas, acompanhar o roteiro cronológico, escolher um jogo e
iniciar blocos no cronômetro compartilhado. O fluxo de início deve se comportar como o
Premium direto, mas sem período de vigência.

O MVP também registra progresso do plano (`não iniciado`, `em andamento`, `concluído`
ou `pulado`) e compara o tempo jogado com a estimativa cadastrada. Jogos, plataformas,
gerações, ordem e estimativas serão mantidos pelo Django Admin.

## 2. Estado atual confirmado

- `Group`, `Category` e `Activity` já formam a hierarquia operacional grupo → categoria
  → atividade. `Activity.duration` é a duração padrão de um bloco, não a duração total
  esperada de um jogo.
- O fluxo Premium já implementa início direto idempotente, uma única execução aberta por
  `scope_key`, duração de 1 a 720 minutos, timer canônico, conclusão antecipada,
  continuação e retorno à fila.
- `Schedule.execution_origin` distingue `queue`, `premium_direct` e `legacy`.
  `queue_item` já é anulável e `planned_duration_seconds` já é a fonte do timer.
- `GoalCompletion` preserva atividade, categoria, grupo, duração efetiva e origem da
  execução. Ele é a melhor fonte para os totais concluídos do catálogo.
- Sessões `premium_direct` não consomem cotas da fila, mas entram em histórico e metas.
  RetroGames deve seguir a mesma regra.
- O Django Admin atual cadastra grupos, categorias e atividades, porém não há plataforma,
  ordem cronológica, estimativa total do jogo ou estado pessoal do roteiro.
- A autenticação segue `HasAPIKey`; `build_scope_key(request)` representa a identidade
  lógica utilizada por execuções, metas e acompanhamento Premium.
- O banco possui migrações até `0019`; a implementação desta spec deverá gerar uma nova
  migração, sem editar migrações já aplicadas.

## 3. Decisões de domínio

### 3.1 Reutilizar o domínio de atividades

Um jogo retrô será uma `Activity` normal associada a metadados `RetroGame`. Isso evita
duplicar nome, descrição, duração do bloco, histórico, metas e cronômetro.

- O grupo operacional terá nome inicial **Retrogames** e `is_retro_catalog=true`.
- Cada `Category` desse grupo representa uma geração: “2ª geração”, “3ª geração” etc.
- `RetroPlatform` introduz o nível que falta entre categoria e atividade.
- `RetroGame` liga uma plataforma a uma `Activity` e guarda dados do planejamento.
- `Activity.duration` continua significando minutos do próximo bloco.
- `RetroGame.estimated_main_minutes` significa tempo estimado para a campanha/meta do
  roteiro. Nunca copiar um valor sobre o outro implicitamente.

Não inferir o catálogo pelo texto “Retrogames”. O booleano no grupo é a identidade
estável. Uma `UniqueConstraint` condicional permite no máximo um grupo marcado como
catálogo retrô no MVP.

### 3.2 Ordem cronológica

O backend devolve a ordem pronta. O cliente não deve conhecer a história das gerações
nem ordenar por nomes localizados.

1. `Category.retro_sort_order` dentro do grupo retrô;
2. `RetroPlatform.sort_order`, depois `release_year` e nome;
3. `RetroGame.sort_order`, depois `release_year` e nome da Activity.

Adicionar `retro_sort_order` opcional a `Category`, usado somente quando seu grupo for o
catálogo retrô. Categorias comuns permanecem inalteradas.

### 3.3 Essencial, complementar e metas especiais

Cada jogo possui:

- `tier`: `essential` ou `complementary`;
- `estimated_main_minutes` positivo;
- `play_goal`: texto curto opcional, por exemplo “10 partidas” ou “campanha principal”;
- `release_year` opcional;
- `sort_order` e `active`;
- `cover_url` opcional no MVP; armazenamento/upload de imagens fica fora desta etapa.

O tempo estimado é editorial e compartilhado. O tempo jogado e o estado do roteiro são
por `scope_key`.

### 3.4 Progresso pessoal

`RetroGameProgress` possui estados:

- `not_started`: estado calculado quando ainda não existe registro nem sessão;
- `in_progress`: criado automaticamente na primeira sessão direta ou definido pelo usuário;
- `completed`: conclusão manual do roteiro;
- `skipped`: abandono consciente, sem apagar tempo já registrado.

Somente `in_progress`, `completed` e `skipped` precisam ser persistidos. O registro terá
`scope_key`, jogo, estado, `started_at`, `completed_at`, `version` e timestamps. Unicidade
por `(scope_key, retro_game)`.

O backend não marca um jogo como concluído apenas porque o tempo estimado foi atingido.
Estimativa não prova término. O usuário confirma a conclusão. Uma nova sessão em jogo
`completed` ou `skipped` não altera silenciosamente o estado; a resposta indica o estado
atual e a UI pode oferecer retomada explícita.

### 3.5 Tempo jogado

O total consolidado de um jogo soma `GoalCompletion.duration_seconds` do mesmo
`scope_key` e `activity_id_snapshot`, independentemente de a sessão ter começado pela
fila, Premium ou RetroGames. Assim, tempo real já registrado para a mesma Activity não
é perdido.

- Usar `duration_seconds`; para fatos legados sem segundos, usar
  `duration_minutes * 60` e informar `coverage=partial`.
- Sessões abertas aparecem separadamente como `open_estimate_seconds` e não entram no
  consolidado.
- `progress_percent = played_seconds / (estimated_main_minutes * 60) * 100`, sem limitar
  a 100%. Estimativa zero não será permitida.
- Reads do catálogo são livres de efeitos colaterais e não reconciliam timers.

## 4. Modelo de dados proposto

### 4.1 Alterações em entidades existentes

#### Group

- `is_retro_catalog`: booleano, default `false`, indexado;
- constraint condicional garantindo no máximo um `true`.

#### Category

- `retro_sort_order`: inteiro positivo opcional;
- validação: se preenchido, a categoria deve pertencer ao grupo retrô.

#### Schedule

- novo `ORIGIN_RETRO_DIRECT = "retro_direct"` em `ORIGIN_CHOICES`;
- `retro_game`: FK opcional para `RetroGame`, `PROTECT`, relacionada às sessões;
- constraint lógica:
  - origem `retro_direct` exige `retro_game`, `queue_item=null` e
    `premium_period=null`;
  - outras origens não devem receber `retro_game`, exceto futura migração explícita;
- `continued_from` e `return_group` são reutilizados sem alteração.

Revisar `History.save()` e todos os cálculos de cota: `retro_direct`, assim como
`premium_direct`, não incrementa `Activity.executions_today`, não consome execução de
categoria, não reserva minutos do grupo e não altera fila. Ele continua gerando
`History` e `GoalCompletion`, portanto conta em metas semanais e estatísticas gerais.

### 4.2 RetroPlatform

| Campo | Regra |
| --- | --- |
| `id` | PK |
| `generation` | FK `Category`, `PROTECT` |
| `name` | até 100 caracteres |
| `slug` | único e estável |
| `manufacturer` | opcional |
| `release_year` | opcional, 1970–2100 |
| `sort_order` | inteiro positivo |
| `active` | default `true` |
| timestamps | criação e atualização |

Unicidade adicional `(generation, name)`. A geração deve pertencer ao grupo com
`is_retro_catalog=true`.

### 4.3 RetroGame

| Campo | Regra |
| --- | --- |
| `id` | PK |
| `activity` | OneToOne `Activity`, `PROTECT` |
| `platform` | FK `RetroPlatform`, `PROTECT` |
| `tier` | `essential` ou `complementary` |
| `estimated_main_minutes` | positivo |
| `play_goal` | texto opcional, até 240 caracteres |
| `release_year` | opcional |
| `sort_order` | inteiro positivo |
| `active` | default `true` |
| `cover_url` | URL opcional |
| timestamps | criação e atualização |

Validação obrigatória: `activity.category_id == platform.generation_id`. A Activity
também deve estar ativa para iniciar uma sessão, mas um jogo desativado continua
consultável no histórico administrativo.

### 4.4 RetroGameProgress

| Campo | Regra |
| --- | --- |
| `scope_key` | 64 caracteres |
| `retro_game` | FK `RetroGame`, `PROTECT` |
| `status` | `in_progress`, `completed` ou `skipped` |
| `started_at` | opcional |
| `completed_at` | obrigatório somente para `completed` |
| `version` | positivo, default 1 |
| timestamps | criação e atualização |

Unicidade `(scope_key, retro_game)`. Alteração usa lock/transação e
`expected_version`; conflito retorna `409 stale_progress_version`.

## 5. Django Admin

O cadastro editorial ocorrerá no Admin, não no Flutter.

- `GroupAdmin`: expor `is_retro_catalog` e impedir segundo grupo marcado.
- `CategoryAdmin`: exibir grupo e `retro_sort_order`; filtro por grupo.
- `RetroPlatformAdmin`: busca por nome/slug, filtros por geração/ativo, ordenação e
  autocomplete de geração.
- `RetroGameAdmin`: busca por Activity/plataforma, filtros por geração, plataforma,
  tier e ativo; autocomplete para Activity e plataforma.
- `RetroGameProgressAdmin`: somente suporte/diagnóstico; busca por jogo e filtro por
  estado, sem expor `scope_key` em listagens exportáveis.
- Em `RetroGameAdmin`, bloquear salvamento quando geração da plataforma e categoria da
  Activity divergirem, mostrando erro acionável.
- Não criar automaticamente uma Activity ao salvar RetroGame no MVP. O fluxo é:
  criar/selecionar grupo → categoria → Activity → plataforma → RetroGame.

A migração de dados cria ou reutiliza, de forma idempotente, o grupo `Retrogames`, marca
`is_retro_catalog=true` e não importa automaticamente milhares de ROMs. O roteiro
curado será cadastrado via Admin conforme solicitado; uma importação em lote poderá ser
planejada separadamente.

## 6. Serviços

Criar serviços finos e testáveis:

- `services/retro_catalog.py`: consultas, filtros, ordenação e agregados de tempo;
- `services/retro_execution.py`: início/continuação idempotentes;
- `services/retro_progress.py`: transições versionadas de progresso.

Views e serializers apenas validam transporte e delegam. Evitar copiar integralmente
`premium_execution.py`: extrair utilitários seguros de idempotência/criação de execução
direta quando isso reduzir duplicação sem mudar o contrato Premium.

### 6.1 Início direto

`start_retro()` recebe `retro_game_id`, `scope_key`, `duration_minutes`, `request_id` e
`return_group_id` opcional.

Regras:

1. validar feature flag `RETROGAMES_ENABLED`;
2. validar jogo, plataforma, geração e Activity ativos;
3. validar duração entre 1 e 720 minutos;
4. tratar `(scope_key, request_id)` com `ExecutionIdempotency`;
5. se já existir execução aberta no escopo, retornar conflito com a execução canônica;
6. criar Schedule `retro_direct`, sem item de fila, com snapshots de meta;
7. criar History na mesma transação;
8. criar progresso `in_progress` somente se ainda não existir;
9. não alterar fila nem cotas operacionais.

Continuação exige predecessor concluído, mesma Activity/RetroGame, versão esperada e
ausência de sucessor. Deve usar a rota genérica já existente de continuação, despachando
por `execution_origin`, ou uma função compartilhada chamada por ela. Premium e fila não
podem sofrer regressão.

## 7. Contratos HTTP

Todas as rotas exigem `HasAPIKey`. O servidor deriva `scope_key`; nunca o recebe nem o
devolve.

### 7.1 Catálogo

| Método e rota | Resultado |
| --- | --- |
| `GET /api/retro-generations/` | gerações ativas em ordem |
| `GET /api/retro-platforms/?generation_id=<id>` | plataformas da geração |
| `GET /api/retro-games/` | jogos paginados com progresso e tempo |
| `GET /api/retro-games/<id>/` | detalhe do jogo |
| `GET /api/retro-games/<id>/sessions/` | sessões concluídas, paginadas |

Filtros de jogos: `generation_id`, `platform_id`, `tier`, `status`, `search` e
`active`. Padrão: ativos, 30 por página, máximo 100. Ordenação é sempre a cronológica
editorial; busca não muda essa ordem.

Exemplo resumido de jogo:

```json
{
  "id": 81,
  "activity_id": 420,
  "name": "Super Mario World",
  "description": "",
  "default_block_minutes": 60,
  "generation": {"id": 14, "name": "4ª geração", "sort_order": 4},
  "platform": {"id": 9, "name": "Super Nintendo", "slug": "snes"},
  "tier": "essential",
  "estimated_main_minutes": 360,
  "play_goal": "Campanha principal",
  "release_year": 1990,
  "cover_url": null,
  "status": "in_progress",
  "progress_version": 2,
  "played_seconds": 5400,
  "open_estimate_seconds": 0,
  "progress_percent": 25.0,
  "coverage": "complete",
  "can_start": true
}
```

### 7.2 Iniciar jogo

```http
POST /api/retro-games/81/start/
```

```json
{
  "duration_minutes": 60,
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "return_group_id": 3
}
```

Resposta `201` na criação ou `200` no replay idempotente: contrato de
`ActivityExecutionSerializer`, com:

```json
{
  "execution_origin": "retro_direct",
  "queue_item_id": null,
  "premium_period_id": null,
  "retro_game_id": 81,
  "planned_duration_seconds": 3600
}
```

### 7.3 Atualizar progresso

```http
PATCH /api/retro-games/81/progress/
```

```json
{"status": "completed", "expected_version": 2}
```

Resposta inclui `status`, `started_at`, `completed_at` e nova `version`. Se não houver
registro, `expected_version` deve ser `0`. Repetir exatamente a intenção atual pode
retornar `200` sem incrementar versão; payload diferente com versão antiga retorna 409.

### 7.4 Erros estáveis

| HTTP | `code` | Situação |
| ---: | --- | --- |
| 400 | `invalid_duration` | duração fora de 1–720 |
| 400 | `retro_hierarchy_invalid` | Activity e plataforma em gerações distintas |
| 404 | `retro_game_not_found` | inexistente/inacessível |
| 409 | `active_execution_conflict` | outro timer aberto |
| 409 | `idempotency_payload_conflict` | chave reutilizada com payload diferente |
| 409 | `stale_progress_version` | edição concorrente |
| 409 | `continuation_context_conflict` | predecessor de outro fluxo/jogo |
| 422 | `retro_game_inactive` | catálogo ou Activity desativado |
| 503 | `retrogames_disabled` | rollout desligado |

## 8. Migração, rollout e compatibilidade

1. adicionar modelos/campos/constraints sem mudar leituras existentes;
2. migrar e criar/reutilizar grupo `Retrogames` idempotentemente;
3. disponibilizar Admin e cadastrar uma pequena amostra do roteiro;
4. publicar endpoints de leitura com `RETROGAMES_ENABLED=false` bloqueando somente
   novos inícios;
5. publicar Flutter preparado para estado indisponível;
6. habilitar a flag e homologar início, conclusão, continuação e retomada;
7. cadastrar o restante do roteiro pelo Admin.

Clientes antigos ignoram a nova origem porque não a receberão sem iniciar pelo novo
menu. Ao recuperarem uma execução `retro_direct` aberta por outro dispositivo, porém,
podem não reconhecer a decisão pós-sessão. Por isso a habilitação exige frontend
compatível em todos os dispositivos usados pela mesma API key.

Não remover nem alterar contratos Premium. A lista de `Schedule.ORIGIN_CHOICES` deve ser
refletida em `GoalCompletion` e serializers sem invalidar dados existentes.

## 9. Testes e critérios de aceite

### Modelo/Admin

- somente um grupo pode ser catálogo retrô;
- plataforma rejeita categoria fora desse grupo;
- jogo rejeita Activity de geração diferente;
- exclusão de Activity/plataforma usada é protegida;
- filtros e buscas do Admin funcionam sem consultas N+1 evidentes.

### API de leitura

- catálogo respeita ordem geração → plataforma → jogo;
- filtros podem ser combinados e paginação é estável;
- progresso é isolado por `scope_key`;
- totais incluem queue, premium e retro da mesma Activity uma única vez;
- fato legado sem segundos produz cobertura parcial;
- nenhuma leitura cria ou reconcilia registros.

### Execução

- primeiro start retorna 201 e replay idêntico 200 com o mesmo ID;
- outra duração com a mesma chave retorna conflito;
- `retro_direct` não possui item de fila nem período Premium;
- só uma execução fica aberta por escopo, inclusive concorrência real em PostgreSQL;
- conclusão cria History e GoalCompletion uma única vez;
- sessões retrô não consomem limite de categoria/grupo nem alteram fila;
- continuação exige versão e contexto corretos;
- recuperação de timer devolve `retro_game_id`;
- início atualiza `not_started` para `in_progress`, mas não reabre automaticamente
  `completed`/`skipped`.

### Progresso

- alteração usa versão e isola escopos;
- concluir preenche `completed_at`; reabrir remove esse instante;
- atingir 100% de tempo não conclui automaticamente;
- percentual pode exceder 100%.

Validações mínimas de implementação: `python manage.py makemigrations --check`, suíte
isolada em SQLite, testes PostgreSQL de concorrência quando configurados e inspeção da
migração antes do deploy. Nunca executar testes contra banco compartilhado.

## 10. Fora do escopo

- ler ROMs do celular, lançar emulador ou abrir arquivo local;
- sincronizar saves, conquistas ou telemetria do emulador;
- upload/processamento de capas;
- scraping de duração ou metadados externos;
- importar automaticamente os milhares de arquivos existentes;
- ranking social, notas, reviews e múltiplos usuários autenticados;
- detectar automaticamente quando um jogo foi zerado;
- alterar o algoritmo da fila ou o acompanhamento Premium.

## 11. Plano de implementação sugerido

1. modelos, constraints, migração e registros no Admin;
2. services de catálogo e progresso + testes unitários;
3. serializers/viewsets/rotas de leitura + testes de contrato;
4. origem `retro_direct`, extração segura do núcleo de início direto e testes de
   concorrência/idempotência;
5. continuação, conclusão e recuperação do timer;
6. contrato JSON versionado em `docs/contracts/` para consumo do Flutter;
7. carga manual de uma geração piloto e homologação antes do catálogo completo.
