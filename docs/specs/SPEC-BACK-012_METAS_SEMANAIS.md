# SPEC-BACK-012 — Metas semanais no backend

## 1. Status e objetivo

Planejamento elaborado e backend implementado em 2026-09-09.
Detalha a primeira etapa do [roadmap](ROADMAP_EVOLUCAO_APP.md).
As regras abaixo descrevem o backend implementado. Migração e carga histórica no
ambiente de destino continuam como etapas de implantação; não foram executadas no
banco real nesta task. O frontend permanece fora desta entrega.

Permitir cadastrar metas recorrentes semanais de minutos ou sessões concluídas,
por grupo ou categoria, e consultar progresso sem interferir nas filas e nos
limites diários. Frontend, gráficos, notificações e comparação entre semanas
ficam fora desta entrega.

## 2. Evidências do código atual

- `models.py`: History nasce com duração e término nulos; Schedule possui
  `scope_key`, estado e `completed_at`. A relação History/Schedule é 1:1.
- `services/activity_execution.py`: `complete_schedule` persiste duração em minutos
  inteiros, truncados, e usa o menor instante entre agora e o término previsto.
  Conclusão antecipada já é possível. Repetir uma conclusão é idempotente.
- `reconcile_schedule` conclui execuções vencidas quando elas são consultadas.
  O término persistido pode ser anterior ao momento da reconciliação.
- `build_scope_key` calcula SHA-256 do Authorization completo. As views usam
  HasAPIKey; não existe aqui uma identidade de usuário estável para as execuções.
- O histórico HTTP atual não filtra por escopo e resolve categoria/grupo pelos
  vínculos atuais da atividade. Não deve ser reutilizado para calcular metas.
- Há exclusões em cascata em Activity, Schedule e History. Os registros atuais
  não garantem preservação de métricas após exclusão ou reclassificação.
- O fuso configurado é `America/Sao_Paulo`, com `USE_TZ=True`.

## 3. Decisões funcionais propostas

### Período e métricas

- Semana começa segunda-feira às 00h no fuso `America/Sao_Paulo` e termina na
  segunda seguinte, exclusiva. Converter limites locais para UTC nas consultas.
- A conclusão determina a semana, mesmo se a sessão começou na semana anterior.
- `minutes`: soma das durações inteiras registradas; nunca usar Activity.duration
  para recalcular execução concluída.
- `sessions`: quantidade de conclusões válidas, inclusive conclusão antecipada
  com zero minutos, preservando a semântica atual do app.
- Sessões abertas, canceladas e expiradas não contam. Ausência de History ou
  duração nula/negativa torna um registro legado inválido para ambas as métricas.
- `remaining = max(target - achieved, 0)`; percentual com duas casas decimais,
  arredondamento decimal half-up, sem limitar a 100%; atingida quando realizado
  é maior ou igual ao alvo. Armazenar somente alvo e fatos, sem contador de progresso.

### Recorrência, edição e desativação

- A meta criada vale desde a segunda-feira da semana atual e inclui as conclusões
  elegíveis anteriores ao cadastro naquela semana.
- Alterar o alvo afeta a semana atual e as futuras, preservando as anteriores.
- Desativar afeta a semana atual e as futuras; semanas anteriores continuam
  consultáveis. Reativar segue a mesma regra.
- Métrica e destino são imutáveis: para mudá-los, desativar a meta e criar outra.
- Uma única meta por `(scope_key, métrica, destino)`, inclusive inativa: reativar
  a existente evita duplicatas. Metas de minutos e sessões podem coexistir.
- Metas de grupo e categoria podem se sobrepor. Cada uma conta a sessão uma vez;
  não oferecer soma global de metas sobrepostas.
- Grupo Todos significa qualquer grupo de origem, não somente sessões iniciadas
  pela fila Todos. Metas de grupos específicos usam o grupo da categoria da
  atividade no início da execução, independentemente da fila escolhida.

### Identidade e compatibilidade

Reutilizar HasAPIKey e `build_scope_key` em todas as novas rotas. O servidor determina
o escopo, nunca o aceita no JSON e não o devolve. Consulta por ID de outro escopo
retorna 404. Não aceitar chamadas sem chave válida.

Uma chave compartilhada representa metas compartilhadas; trocar o Authorization
muda o escopo. Esta entrega não cria contas nem migra chaves. Antes de implementar,
confirmar no cliente se a mesma chave é compartilhada entre dispositivos e se isso
corresponde ao uso desejado. Caso se queira metas por pessoa com login, revisar a
identidade antes de persistir metas. Não mapear históricos de escopo vazio para a
chave atual por inferência.

## 4. Modelo de dados proposto

### WeeklyGoal

- `id`, `scope_key` (64 caracteres), `metric` (`minutes` ou `sessions`).
- `group` e `category`: FKs opcionais com PROTECT; exatamente uma preenchida,
  garantida por CheckConstraint. Não excluir destinos de metas; preferir desativação.
- `is_all_groups`: snapshot de `Group.is_default` na criação, somente para destino
  grupo, para a meta Todos não mudar de significado se o grupo padrão mudar.
- `created_at`, `updated_at` e `version` positiva para controle de edição concorrente.
- UniqueConstraints condicionais por escopo/métrica/grupo e escopo/métrica/categoria.

### WeeklyGoalRevision

- FK para WeeklyGoal, `effective_week` (DateField, segunda-feira), `target` inteiro
  positivo, `active` booleano e timestamps.
- Unicidade `(goal, effective_week)` e constraint de alvo positivo.
- A revisão vigente é a última com `effective_week <= semana consultada`.
- Criar revisão na semana atual ao editar; se já existir, atualizá-la dentro da
  mesma transação. A primeira revisão nasce junto com a meta.
- Não precisa de job semanal nem materialização de períodos vazios. Uma semana
  anterior à primeira revisão não tem meta vigente.

### Contexto histórico e GoalCompletion

Adicionar em Schedule dois IDs escalares opcionais: `goal_category_id_snapshot`
e `goal_group_id_snapshot`, preenchidos no início da execução. Não são FKs: mover
ou excluir entidades não deve modificar a classificação histórica.

Criar GoalCompletion como registro imutável de conclusão para métricas:

- `source_schedule_id`: inteiro único, preservado mesmo se Schedule for excluído.
- `scope_key`, `category_id_snapshot`, `group_id_snapshot`, `completed_at`,
  `duration_minutes` não negativa e `created_at`.
- `context_source`: `execution_start` ou `legacy_current`, explicitando a origem.
- Índices compostos `(scope_key, completed_at)`,
  `(scope_key, group_id_snapshot, completed_at)` e
  `(scope_key, category_id_snapshot, completed_at)`.

Essa pequena cópia histórica é necessária para não perder progresso em cascatas
ou reclassificações. Não contém notas nem credenciais. Não é um contador por meta:
uma conclusão pode alimentar várias metas, e novas metas podem aproveitar a semana.
Correções manuais de History posteriores não alteram automaticamente GoalCompletion;
uma eventual ferramenta de correção auditada fica fora deste MVP.

## 5. Escrita e consistência

No início, capturar categoria e grupo dentro da transação existente. Na conclusão,
criar GoalCompletion na mesma transação de History/Schedule e antes do retorno.
Usar unicidade de source_schedule_id para assegurar idempotência; falha no registro
deve reverter a conclusão completa. Nunca incrementar meta individualmente.

Repetição de conclusão já concluída pode garantir o registro ausente para execução
válida durante a transição, sem recontar ou modificar um fato existente.
Validar estado de origem: somente preparing/running podem transitar para completed;
cancelled/expired devem retornar conflito e não gerar fato. Revisar os chamadores
para esse guard, pois o serviço atual só faz retorno antecipado para completed.

Criação/edição de metas e revisões são atômicas. PATCH exige `expected_version`;
atualização condicional ou lock na meta impede perda de edição. Versão obsoleta
retorna 409. Capturar a semana uma vez por requisição para consistência na virada.

GET de progresso é estritamente leitura e não chama reconciliação. Retornar
`pending_reconciliation_count` para execuções abertas e vencidas do mesmo escopo
cujo término previsto cai no período consultado, sem filtro por destino (indicador
do período inteiro). Enquanto houver pendências, o progresso contém somente
conclusões persistidas. O cliente pode usar o POST de reconciliação já existente
e consultar novamente; um worker automático não integra este MVP.

## 6. Contratos HTTP propostos

Todas as rotas abaixo exigem chave válida e aplicam o escopo no queryset.

| Método e rota | Resultado |
| --- | --- |
| POST `/api/weekly-goals/` | 201, cria meta e primeira revisão |
| GET `/api/weekly-goals/` | 200, lista paginada, inclusive inativas |
| GET `/api/weekly-goals/<id>/` | 200, configuração vigente e versão |
| PATCH `/api/weekly-goals/<id>/` | 200, altera target e/ou active com expected_version |
| GET `/api/weekly-goals/progress/?week_start=2026-09-07` | 200, progresso paginado da semana |

Não oferecer DELETE ou PUT no MVP (405). Usar paginação page/page_size, padrão 20,
máximo 100, ordenada por id. Lista aceita `active=true|false`; omitir inclui ambas.
Progresso inclui somente metas com revisão vigente ativa no período solicitado.
Lista vazia retorna 200 com results vazio.

POST de exemplo:

```json
{"metric":"minutes","group_id":3,"target":240}
```

Para categoria, enviar `category_id` no lugar de `group_id`. POST inicia active=true.
Campos desconhecidos ou controlados pelo servidor devem ser rejeitados com 400,
incluindo scope_key, version e is_all_groups. PATCH aceita somente target, active e
expected_version; deve conter pelo menos uma mudança.

Exemplo de configuração retornada por POST/GET/PATCH:

```json
{
  "id":1,"metric":"minutes","group_id":3,"category_id":null,
  "is_all_groups":false,"target":240,"active":true,
  "effective_week":"2026-09-07","version":1
}
```

Resposta de progresso (exemplo com 2 sessões de 60 minutos):

```json
{
  "week_start":"2026-09-07",
  "week_end_exclusive":"2026-09-14",
  "timezone":"America/Sao_Paulo",
  "as_of":"2026-09-09T15:00:00Z",
  "pending_reconciliation_count":0,
  "count":1,"next":null,"previous":null,
  "results":[{
    "goal_id":1,"metric":"minutes","group_id":3,"category_id":null,
    "target":240,"achieved":120,"remaining":120,
    "progress_percent":"50.00","is_achieved":false
  }]
}
```

`week_start` omitido usa semana atual. Valor deve ser data ISO de segunda-feira,
nunca posterior à semana atual; erro 400 caso contrário. Semana anterior à criação
retorna lista vazia. Percentual é string decimal estável. Datas/instantes e limites
do período são calculados uma única vez; `as_of` indica o instante da consulta.

Erros de domínio usam `{"code":"...","detail":"..."}`, com `fields` opcional:
400 `invalid_goal`/`invalid_week`, 404 `goal_not_found`, 409 `goal_already_exists`
ou `goal_version_conflict`. Destino inexistente no POST é 400. Falhas de chave
mantêm o comportamento HasAPIKey/DRF existente, sem novo formato obrigatório.

## 7. Organização e arquivos

- `models.py`: modelos, snapshots, constraints e índices descritos acima.
- `services/weekly_goals.py`: configuração, revisões, períodos e regras de progresso.
- `repositories/weekly_goals.py` e `repositories/__init__.py`: seleção por escopo,
  revisões vigentes e agregações; evitar consultas por meta em laço.
- `services/activity_execution.py`: snapshots e gravação idempotente do fato.
- `serializers.py`, `views.py`, `urls.py`: serializers próprios e WeeklyGoalViewSet
  com métodos explicitamente limitados; registrar ação progress antes do detalhe.
- `admin.py`: consulta das metas/revisões/fatos somente leitura, evitando contornar
  versionamento; não modificar os admins existentes nesta entrega.
- Novas migrations, comando de carga histórica e testes dedicados em
  `apps/pomodoro/test_weekly_goals.py`, seguindo a organização local.
- README e coleção Postman: novos contratos, exemplos e comandos operacionais.

Agregar fatos por categoria/grupo e total do escopo em consultas limitadas ao
período. Reutilizar os resultados para metas da página; não juntar revisões ou filas
na agregação de fatos, evitando multiplicação de SUM/COUNT.

## 8. Migração, legado e entrega

1. Criar migrations aditivas (numeração após conferir a folha vigente), com tabelas
   vazias e snapshots opcionais. Não reescrever histórico dentro da migration.
2. Publicar captura de snapshots e fatos antes de liberar as rotas de metas.
3. Executar comando `backfill_goal_completions --dry-run`, depois carga em lotes
   idempotente por source_schedule_id. Em cada lote, usar dados coerentes e não
   sobrescrever fatos já gravados pela aplicação.
4. Legado exige state=completed, completed=true, completed_at/end_time presentes
   e iguais, duração não negativa, intervalo temporal válido e escopo não vazio
   nem anonymous. Não reconstruir duração pela configuração atual da atividade.
5. Quando faltar snapshot, usar categoria/grupo atuais e marcar legacy_current.
   Não inventar contexto do início. Relatar totais elegíveis, inseridos, existentes
   e excluídos por motivo, sem imprimir credenciais ou scope_key.
6. Sessões abertas no momento do deploy usam contexto atual ao concluir se não
   tiverem snapshot, também marcadas como legado. Documentar essa limitação.
7. Conferir contagens e somas por período em ambiente controlado, então liberar
   endpoints e atualizar documentação de cobertura histórica.

A carga de fatos não usa History.save(), pois ele possui efeito em contadores.
Reexecuções do comando não mudam classificação ou duração já capturadas.
O dry-run não escreve. Dados inconsistentes permanecem intactos e excluídos.

Rollback operacional: retirar as rotas/voltar o código compatível e preservar
tabelas novas. Não reverter migrations com dados para resolver falha de aplicação.
Reaplicar código e rodar carga idempotente cobre conclusões do intervalo anterior;
contexto histórico desse intervalo terá a limitação de legado.

## 9. Validação e sequência de implementação

Implementar em blocos coesos, nesta ordem:

1. Modelos e migrations; validar constraints, revisão semanal e unicidades.
2. Captura de fatos, guard de estados e carga legada com dry-run.
3. Serviços de metas, versionamento e agregações.
4. API, documentação e regressão dos contratos anteriores.

Casos obrigatórios:

- 120/240 minutos = 50%; 3/3 sessões atingidas; sobrecumprimento e zero minutos.
- Virada da semana em São Paulo, ano novo e conclusão reconciliada tardiamente.
- Exclusão, reclassificação ou mudança de duração não alteram fatos já gravados.
- Grupo específico e Todos contam sessões pela origem, inclusive em fila de revisão.
- Duas chaves válidas não consultam/alteram metas ou fatos uma da outra; sem chave
  é negado; IDs estrangeiros retornam 404; injeção de scope_key é rejeitada.
- Criação retroativa na semana, edição sem modificar semanas anteriores,
  desativação/reativação e duplicidade por destino/métrica.
- Repetição de conclusão não duplica fato; erro ao gravá-lo reverte a transação;
  cancelled/expired não viram completed; versões obsoletas retornam conflito.
- Progresso não altera nenhuma tabela; pendências de reconciliação são informadas.
- Carga repetida e dry-run, legado inconsistente, escopo ausente e captura simultânea.
- Paginação, datas inválidas, campos desconhecidos e agregação sem N+1 por meta.

Comandos planejados usando settings explicitamente de teste:

```bash
DJANGO_SETTINGS_MODULE=config.settings.test poetry run python manage.py check
DJANGO_SETTINGS_MODULE=config.settings.test poetry run python manage.py makemigrations --check --dry-run
DJANGO_SETTINGS_MODULE=config.settings.test poetry run python manage.py test apps.pomodoro.test_weekly_goals
DJANGO_SETTINGS_MODULE=config.settings.test poetry run python manage.py test
```

SQLite isolado conforme configuração existente; nunca usar banco real ou compartilhado.
SQLite não comprova locks PostgreSQL. Antes de liberar em produção, validar a corrida
de duas edições e duas conclusões em PostgreSQL descartável dedicado, com configuração
de teste específica, sem alterar o guard SQLite da suíte padrão nem usar credenciais
de desenvolvimento. Registrar essa validação como pendente se não houver ambiente.

## 10. Limites e próximo passo

A proposta decide regras do MVP para tornar a implementação executável. O ponto a
confirmar primeiro é a identidade por chave no cliente; a API não promete isolamento
por pessoa. Os demais limites assumidos são minutos inteiros, cobertura legada com
classificação aproximada e atualização após reconciliação explícita.

Próxima task: aplicar a migration e conferir a carga histórica no ambiente de destino,
então integrar o frontend aos novos contratos.
As funcionalidades 2 e 3 do roadmap continuam fora do escopo. Pausas futuras deverão
alimentar duration_minutes com o tempo de atividade efetivo, sem contar descanso.

## 11. Registro da implementação

- Migration aditiva `0017_weekly_goals`, snapshots no início e fatos de conclusão
  gravados atomicamente. Estados cancelled/expired retornam conflito na conclusão.
- Cadastro, leitura, edição versionada, desativação, reativação e progresso paginado.
- Views novas isoladas em `weekly_goal_views.py`, mantendo o módulo anterior estável.
- Serviços e repository dedicados; os GETs de metas não reconciliam nem gravam dados.
- `backfill_goal_completions` possui dry-run, lotes entre 1 e 10000 e relatório JSON.
  Relata também History de atividade divergente como `activity_mismatch`.
- Admins novos somente leitura; README e coleção Postman atualizados.
- Assumido o escopo existente por Authorization conforme o plano. Login por pessoa
  e transferência de metas entre chaves não foram introduzidos.

Validação: suíte padrão com 142 testes em SQLite isolado (139 passaram e os 3 de locks
foram pulados); os 22 testes de metas passaram em PostgreSQL 16
descartável, incluindo edições concorrentes, conclusões concorrentes e corrida entre
carga histórica e conclusão. Os testes de lock usam `has_select_for_update` e são
pulados automaticamente em SQLite. O guard SQLite da suíte padrão foi preservado;
PostgreSQL foi validado com settings temporários exclusivos para o container sem volumes.
`manage.py check`, `makemigrations --check --dry-run` e `git diff --check` passaram.
O container foi encerrado e removido após a validação.
