# SPEC-BACK-013 — contexto e sinais de atividade das metas semanais

Data: 12/09/2026.

## 1. Objetivo e invariantes

Evoluir `GET /api/weekly-goals/progress/` sem remover campos para que cada meta
tenha um destino descritivo e sinais semanais de pulo. Pulos são contexto: não
alteram `achieved`, `remaining`, `progress_percent`, fila, prioridade ou risco da
meta. O período continua sendo a segunda-feira 00:00 de `America/Sao_Paulo`, com
fim exclusivo na segunda seguinte, e usa o instante confirmado do pulo.

Toda leitura e escrita de fatos usa o `scope_key` derivado de Authorization. A
chave nunca entra ou sai do JSON. O GET de progresso permanece somente leitura.

## 2. Evidências e decisão histórica

`ActivityQueueItem.skipped_at` preserva o instante, mas `item.activity.category`
e `category.group` refletem a classificação atual. `ActivityPreferenceEvent`
também usa FKs com `CASCADE`. Portanto, nenhum dos dois sustenta análise estável
após reclassificação, renome ou exclusão.

Será criada a tabela aditiva e imutável `GoalActivitySkip`, conceitualmente irmã
de `GoalCompletion`, sem FKs para entidades mutáveis. Um registro por
`source_queue_item_id` captura:

- `scope_key`, `skipped_at`, IDs da fila e da atividade;
- IDs, nomes e cores de categoria e grupo no instante do pulo;
- nome da atividade e modo da fila;
- `context_source=live_skip` para captura transacional ou `legacy_current` para
  carga histórica baseada no contexto atual ainda disponível.

O fato é criado na mesma transação que muda o item para `skipped`. A unicidade do
item mantém a idempotência atual: repetir o endpoint para um item já consumido não
cria outra ação nem outro fato. Falha na captura reverte o pulo.

## 3. Contrato HTTP

Cada resultado de progresso preserva os campos atuais e acrescenta:

- `destination`: `type`, `id`, `name`, `color`, `group_id`, `group_name`;
- `activity_signals.skip_count`: número de fatos de pulo correspondentes;
- `activity_signals.distinct_activities_skipped`: IDs de atividade distintos.

Os nomes e cores de `destination` são os valores atuais do catálogo, coerentes
com o destino configurado da meta. Tanto `Group` quanto `Category` possuem cor no
modelo, logo `color` é preenchido em ambos. Os rótulos de `most_skipped` vêm dos
snapshots históricos e não mudam retroativamente.

`?include=activity_signals` acrescenta `most_skipped`, limitado às três atividades
com mais ações, ordenadas por quantidade decrescente, nome e ID. Sem expansão, o
objeto mantém apenas os dois contadores. Um `include` ausente é válido; valores
desconhecidos, vazios, combinados ou repetidos retornam `400 invalid_include`.

As agregações são feitas em lote para todos os cards da página: uma consulta de
conclusões e uma de pulos, ambas limitadas a escopo e período. Os destinos são
carregados com `select_related`; não há consulta por meta ou atividade. Paginação
e todos os campos anteriores permanecem iguais.

Semântica:

- meta de categoria casa `category_id_snapshot`;
- meta de grupo casa `group_id_snapshot`;
- grupo “Todos” usa todos os fatos do escopo/período, como conclusões;
- o mesmo fato pode aparecer em uma meta de categoria e uma de grupo, mas cards
  nunca devem ser somados como total global.

## 4. Carga legada e implantação

A migration cria apenas a tabela e índices; não executa backfill. O comando
`backfill_goal_activity_skips` percorre itens realmente `skipped` com
`skipped_at`, preserva fatos existentes, aceita `--dry-run` e lotes de 1 a 10000.
Itens sem `scope_key`, com escopo `anonymous`, sem instante ou inconsistentes são
excluídos e contabilizados por motivo. Nenhum `scope_key`, nome ou dado sensível é
impresso. O contexto legado é explicitamente `legacy_current` e não promete a
classificação original.

Não rodar a migration nem a carga em banco real nesta task. Em implantação,
publicar primeiro o código/migration que captura fatos novos e executar dry-run e
carga em ambiente controlado depois.

## 5. Cobertura da fila: decisão adiada

O modelo permite várias filas ativas por escopo, uma por grupo; a fila “Todos” e
uma fila específica podem conter a mesma atividade, e `skipped_review` representa
reapresentação, não novo planejamento. Contar itens confundiria atividades únicas
com sessões e exigiria escolher arbitrariamente entre filas. Por isso esta task
não publica contador de cobertura. Uma evolução futura deve definir fila(s),
estados (`pending`/`presented`), deduplicação por atividade e tratamento explícito
de revisão antes de criar o contrato.

## 6. Comparação e distribuição: contrato futuro

Não serão implementadas nesta etapa. Uma série semanal deverá carregar estado de
existência da meta (`not_created`, `inactive`, `active`), alvo e realizado por
semana, além de marcar a semana atual como parcial. Variação percentual será nula
quando a base anterior for zero.

Distribuição deverá partir de `GoalCompletion` deduplicado por
`source_schedule_id`, oferecer dimensão `group` **ou** `category` por requisição e
nunca somar metas sobrepostas nem incluir “Todos” ao lado de grupos específicos.
Nomes/cores históricos exigirão snapshots próprios antes desse contrato.

## 7. Validação

Cobrir fatos ao vivo, idempotência, rollback, carga seca/repetida, limites da
semana, escopo, reclassificação/renome/exclusão, categoria/grupo/Todos,
sobreposição, fila normal e rejeição da revisão, expansão, paginação, campos
legados e contagem constante de consultas. Executar `check`, conferência de
migrations, testes focados e suíte completa somente com
`config.settings.test`/SQLite isolado.
