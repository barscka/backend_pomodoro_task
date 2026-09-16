# SPEC-014 — Premium: escolha livre, continuidade e acompanhamento por período

Data: 2026-09-16. Status: backend implementado; Flutter e liberação da flag pendentes.

## 1. Objetivo

Permitir escolher um jogo premium e jogar repetidamente, sem depender da posição
na fila. Ao terminar um bloco, escolher continuar no jogo, voltar à fila do grupo
ou encerrar. Medir o tempo registrado em cada jogo dentro de seu período premium,
comparando-o com a referência pessoal de 5 horas diárias de gameplay.

Atende tanto a períodos pagos (Black Desert, War Thunder, Path of Exile 2) quanto
a períodos de foco pessoal (por exemplo, Age of Empires 2). A classificação premium
não significa necessariamente assinatura paga, nem precisa ter preço cadastrado.

A referência de 5 horas não é uma obrigação ou trava. O app registra sessões
iniciadas pelo usuário; não detecta automaticamente se o jogo está aberto ou se
há atividade real no jogo. Não apresentar o contador como telemetria do jogo.

## 2. Base confirmada nos dois repositórios

Backend consultado: commit `31acb6d`. Frontend consultado: commit `3b3e65c`.

- Premium já é priorizado na ordenação e reconciliação da fila, dentro dos grupos
  elegíveis. Item apresentado/iniciado é preservado; prioridade não garante início.
- `activity_is_eligible`/`eligible_activities` aplicam limites de categoria, saldo
  do grupo e exclusão de atividades já concluídas no dia. Há uma ocorrência por
  atividade em cada fila. Isso não oferece repetição manual livre.
- `POST activities/<id>/start/` exige `queue_item_id` mesmo para premium.
- Schedule já aceita queue_item nulo e mantém uma execução aberta por scope_key.
- A vigência atual está apenas em `Activity.premium_from/premium_until`; expiração
  desliga o booleano. Editar essas datas não preserva ciclos de assinatura anteriores.
- Os contadores de fila usam History e, para minutos reservados, Activity.duration.
  Inserir sessões manuais sem revisar essas consultas consumiria os limites da fila.
- GoalCompletion preserva fatos para metas, mas não possui todos os campos necessários
  para atribuir horas a uma atividade e recortar intervalos de vigência com precisão.
- Flutter usa Provider/ChangeNotifier, PomodoroController e ApiService. Os modelos
  ActivityExecution/ActiveActivitySnapshot exigem queueItemId inteiro.
- `_applyExecution` cria ActivityQueueItem a partir de qualquer execução;
  `_handleExecutionCompleted` limpa a execução e carrega a próxima da fila.
- O progresso visual usa Activity.duration, insuficiente para duração personalizada.
- Mobile tem Home, Histórico e Fila; metas ficam acessíveis por ação. HomeScreen
  usa layout mobile nas plataformas móveis ou abaixo de 720 px; desktop tem um
  segundo breakpoint em 1200 px.
- Metas semanais e sinais de pulos já existem nos dois lados. Devem continuar funcionando.

Não foi feita auditoria dos dados reais nem confirmado quais jogos estão marcados
premium no banco. As causas acima são restrições verificadas no código, não um
diagnóstico individual de cada jogo que deixou de aparecer.

## 3. Comportamento proposto

### 3.1 Área Premium

Adicionar um destino permanente chamado **Premium**, com duas abas:

- **Jogar**: períodos vigentes, futuros e encerrados, com vigentes primeiro.
- **Acompanhamento**: resumo agregado, evolução diária e histórico por jogo/período.

Cada card vigente mostra jogo, tipo (Pago/Foco), início/fim, dias restantes,
horas registradas no período, tempo hoje, última sessão e botão **Jogar agora**.
Permitir ordenar por término mais próximo, menos horas no período ou nome.
“Ainda não jogado neste período” ajuda a identificar jogos esquecidos sem afirmar
que houve perda financeira ou que uma quantidade mínima de horas é obrigatória.

O catálogo Premium não depende do filtro de grupo selecionado na Home. O grupo
de origem aparece no card para manter o contexto. Períodos futuros/encerrados
podem ser consultados, mas não iniciados pelo modo premium.

### 3.2 Iniciar, repetir e encerrar

1. Escolher um premium vigente e tocar em Jogar agora.
2. Escolher duração: padrão da atividade, atalhos de 15/30/60 minutos e valor
   personalizado. MVP: inteiro entre 1 e 720 minutos por bloco; repetição sem limite
   de quantidade. Esse máximo é técnico por sessão, não uma cota diária.
3. Backend cria execução `premium_direct`, sem criar ou consumir item de fila.
4. Exibir timer canônico, horas acumuladas e opção **Encerrar sessão** antecipadamente.
5. Ao concluir, mostrar o tempo registrado e três ações:
   **Continuar neste jogo**, **Voltar à fila** e **Encerrar por hoje**.
6. Continuar inicia outro bloco com duração ajustável e vínculo à execução anterior.
   Não prolonga o histórico já concluído nem exige reconstruir a fila.

Sem limite de repetições por dia/categoria no início premium direto. A duração da
atividade continua sendo padrão, não é alterada por escolher um bloco diferente.
Somente uma execução aberta por escopo, incluindo fila e premium. Se outra está
rodando, mostrar qual é e oferecer abrir o timer; não interrompê-la silenciosamente.

No fim do timer, não iniciar outro bloco automaticamente. Tempo aguardando decisão
não é registrado. Notificação é aviso, não confirmação de continuidade. Se o usuário
seguir jogando sem iniciar novo bloco, esse intervalo não será contado no MVP.
Cronômetro sem término e edição manual de horas ficam para evolução posterior.

Se uma sessão premium foi iniciada pela fila, o primeiro bloco consome aquele item
normalmente; **Continuar** cria um bloco direto. Sessões da fila de revisão seguem
o mesmo princípio: não criar pulos ou conclusões artificiais na revisão.

### 3.3 Vigência

- Início e fim são datas no fuso America/Sao_Paulo; a data final é inclusiva.
  Internamente representar `[início 00h, dia seguinte ao fim 00h)`.
- Nova sessão premium exige vigência no instante confirmado pelo servidor.
- Pode atravessar o fim do período: não interromper a execução já iniciada, mas
  contar no relatório premium apenas a parte dentro da vigência.
- Após vencer, desabilitar Continuar como premium e oferecer voltar à fila.
  O jogo continua como atividade normal se elegível; não renovar automaticamente.
- Renovação cria outro período, sem sobrescrever o anterior. Proibir sobreposição
  de períodos do mesmo jogo; períodos de jogos diferentes podem coincidir.
- Período de foco também tem início/fim. O usuário pode renovar o foco depois.
- Corrigir datas/tipo apenas antes do início e sem sessões relacionadas. Depois,
  manter os limites históricos; correções retroativas auditadas ficam fora do MVP.
  Para encerrar antes, registrar um instante de encerramento antecipado distinto
  da data contratada. Preservar ambos, recortando elegibilidade e horas nesse instante.

### 3.4 Relação com a fila e as 5 horas

O modo direto não cria, pula, conclui, recria ou reordena itens da fila. Também não
produz evento de preferência por favorito/pulo. Ao voltar, retomar o grupo de origem
da navegação; a próxima atividade continua submetida às validações normais atuais.

Preservar a ordem relativa e o item apresentado enquanto elegíveis. Uma mudança
legítima de atividade, grupo ou vigência pode invalidá-los; não prometer devolver
sempre o mesmo ID nem ressuscitar uma fila fechada por outro dispositivo.

Decisão para evitar bloqueio indireto: sessões `premium_direct` não consomem cotas
operacionais da fila (execuções por categoria, exclusão por concluída hoje e minutos
reservados por grupo). Entram integralmente no histórico geral, nas metas semanais
e nas estatísticas de gameplay. Sessões `queue` mantêm as regras atuais, inclusive
quando o jogo é premium. Assim, é possível jogar um premium e depois voltar à fila.

Na UI, distinguir **Tempo de gameplay registrado** de **Saldo da fila**. A referência
de 300 minutos/dia é informativa e independente de Group.max_daily_minutes. Não
atribuir 5 horas a cada premium: é uma referência total compartilhada entre os jogos.

## 4. Indicadores e cálculo do tempo

### 4.1 Fonte e recorte

Registrar todas as conclusões com activity_id, contexto de grupo/categoria, origem
da execução e intervalo efetivo. Relatório premium inclui o jogo durante a vigência,
seja a sessão direta ou iniciada pela fila. Não filtrar pelo booleano premium atual.

Para uma sessão `[s,e)` e período `[p0,p1)`, segundos atribuídos:

```text
max(0, min(e, p1) - max(s, p0))
```

Aplicar também o intervalo solicitado pelo relatório. Agrupar dias separando os
intervalos à meia-noite local. Guardar segundos; somar antes de converter para
horas/minutos. Minutos inteiros de GoalCompletion continuam com a semântica antiga
das metas semanais, que atribuem a sessão à semana da conclusão; não reescrevê-los.

Exemplo: sessão de 23h40 a 00h40 no último dia da vigência → 20 minutos no premium,
60 minutos no histórico geral. Se uma renovação começa à meia-noite, os 40 minutos
restantes pertencem ao novo período, sem contar duas vezes a mesma fração.

Sessões abertas: mostrar estimativa separada “em andamento”, limitada ao relógio
do servidor, término previsto e vigência. Somente conclusões confirmadas entram
nos totais consolidados. Canceladas/expiradas não contam; encerramento antecipado
é conclusão com duração efetiva. Reads não reconciliam nem modificam o banco.

### 4.2 Por jogo e período

- Horas no período, sessões com interseção positiva e dias com alguma hora registrada.
- Horas hoje, média diária desde o início, última sessão e dias restantes.
- Percentual do período decorrido, separado do percentual de tempo jogado.
- Participação desse jogo nas horas totais de gameplay no mesmo intervalo.
- Horas do jogo / referência acumulada de 5 h por dia decorrido.
- Horas do jogo / referência total de 5 h por dia de toda a vigência, identificada
  como projeção de disponibilidade, não como meta específica daquele jogo.

“Dias decorridos” inclui dias sem jogar e o dia atual, marcado como parcial. O dia
atual usa a referência diária inteira; explicar isso no detalhe. Para período futuro,
denominador decorrido é zero e média/percentuais são null, exibidos como “—”.
Período encerrado usa todos os dias até seu encerramento, sem continuar aumentando
o denominador. Zero gameplay também produz participação null, não divisão por zero.
Horas/percentuais podem superar a referência; não limitar os dados a 100%.

Exemplo de período de 30 dias, observado no 10º dia:

| Medida | Exemplo |
| --- | --- |
| Tempo no jogo | 20 h |
| Média do jogo por dia decorrido | 2 h/dia |
| Referência acumulada | 10 × 5 h = 50 h |
| Jogo / referência acumulada | 40% |
| Gameplay total registrado nesses 10 dias | 40 h |
| Jogo / gameplay real | 50% |
| Referência total da vigência | 30 × 5 h = 150 h |
| Jogo / referência total | 13,33% |

Não chamar 40% ou 13,33% de retorno financeiro. O app não conhece valor dos benefícios,
diversão ou obrigação de jogar. Valor pago e custo por hora são evolução opcional,
com cadastro manual e sem conclusão automática de “dinheiro perdido”.

### 4.3 Agregado Premium

Selecionar intervalo (hoje, últimos 7/30 dias ou personalizado) e grupos de gameplay.
Mostrar horas premium, horas nos demais jogos, gameplay total, referência do intervalo
e distribuição por jogo. Gráfico diário empilhado: premium + demais jogos, com linha
de referência em 5 h. Lista de sessões permite filtrar jogo, período e origem.

O agregado é a união dos segmentos premium, nunca a soma dos denominadores de cada
assinatura. Três premiums simultâneos não transformam 5 h/dia em 15 h/dia. Somar
horas de cada jogo é válido se os intervalos não duplicarem sessões do mesmo escopo.
Sessão atravessando dois períodos conta uma vez no total de sessões agregadas, embora
possa aparecer nos dois detalhes com suas respectivas frações de tempo.

Os grupos que representam gameplay devem ser selecionados explicitamente; não
inferir pelo nome Games nem incluir estudo/trabalho. Detalhes mantêm horas do jogo
mesmo se seu grupo estiver fora dessa seleção, mas participação no gameplay fica
indisponível com explicação até ajustar os grupos. A referência fica identificada
como configuração atual; mudar grupos/5 h altera comparativos, nunca fatos históricos.

## 5. Experiência Flutter: mobile e desktop

### Mobile

- Adicionar Premium como quarta opção da NavigationBar, preservando Home, Histórico
  e Fila. Manter acesso existente a Metas. Não esconder a ação principal num submenu.
- Cards em coluna com CTA Jogar agora; formulário de duração em bottom sheet.
- Ao iniciar, abrir o timer compartilhado com badge Premium e nome do jogo. Manter
  navegação disponível; execução não pertence ao ciclo de vida da tela.
- Ao concluir, mostrar painel de decisão persistente com as três ações. Um aviso
  dispensado não pode iniciar fila nem perder a opção de continuar.
- Acompanhamento: resumo em cards, gráfico rolável e lista diária; detalhe do período
  em tela própria. Exibir horas/minutos, não exigir interpretar número decimal.
- Respeitar áreas seguras, fonte ampliada, alvos de toque de 48 px e estados offline.

### Desktop

- Acesso Premium na barra da Home. Em largura >=1200 px, tela Premium com lista de
  jogos à esquerda e detalhe/timer/acompanhamento do selecionado à direita.
- Em 720–1199 px, cards e detalhe empilhados; abaixo disso, fluxo mobile existente.
  Usar constraints de layout, evitando larguras fixas que cortem conteúdo.
- Durante execução, manter visíveis jogo, tempo restante, horas no período e
  contexto de retorno à fila. Clique simples seleciona card; Jogar agora inicia.
- Histórico tabular no desktop e lista no mobile compartilham os mesmos filtros e
  dados. Suportar teclado, foco visível e labels acessíveis; cor não é o único status.

### Estado compartilhado, rede e recuperação

- Um único coordenador de execução (PomodoroController ou serviço extraído dele),
  reutilizando relógio, armazenamento e notificações. PremiumController gerencia
  catálogo/relatórios, não outro timer concorrente.
- ActivityExecution e ActiveActivitySnapshot passam a ter origem discriminada,
  queueItemId nullable e contexto premium. Para origem queue, o ID continua obrigatório.
- Só converter execução em ActivityQueueItem quando origem for queue. Não preencher
  IDs fictícios (0/-1) nem usar o item apresentado como fallback de uma sessão direta.
- Timer usa duração da execução (`planned_duration_seconds`), não Activity.duration.
- Guardar versão do formato do snapshot, idempotency key de início pendente, grupo
  para retorno e contexto da sessão concluída ainda aguardando decisão. Ler snapshots
  antigos como queue; sincronizar antes de habilitar ações.
- Tela de conclusão premium não chama `_loadNextQueueItem` automaticamente. Ao clicar
  Voltar à fila, recuperar o estado atual daquele grupo sem solicitar recriação.
- Reabrir/reconectar consulta execução canônica. Timeout de início/continuação repete
  a mesma chave; não assumir que a sessão não foi criada. Offline mostra cache como
  desatualizado e não inventa inícios, conclusões ou horas consolidadas.
- Conclusão atualiza histórico geral, metas e relatório premium uma única vez por
  execution_id/version. Respostas antigas não substituem uma sessão nova.
- Dois dispositivos com mesma chave veem a mesma execução. Conflito abre o timer
  canônico e preserva a fila localmente selecionada como contexto de navegação.

## 6. Modelo e serviços backend propostos

### PremiumPeriod

Nova entidade ligada à Activity com PROTECT: id, kind (`paid`/`focus`), título,
starts_on, ends_on, timezone fixo, ended_early_at opcional, version e timestamps.
Não apagar períodos usados. Não sobrepor vigências efetivas do mesmo jogo; criar/
renovar sob lock da Activity para impedir sobreposição concorrente.

Períodos são metadados do catálogo compartilhado, como Activity atualmente; acesso
exige HasAPIKey. Sessões, relatórios e preferências são isolados por scope_key.
Não apresentar esse catálogo como privado por pessoa. Não introduzir login nesta etapa.

Os campos premium antigos viram projeção de compatibilidade da vigência corrente.
Um serviço único resolve premium em filas e seleção direta. PATCH/Admin legados
devem delegar à criação/edição de período ou rejeitar mudanças que sobrescreveriam
histórico. O job de expiração atual não pode apagar períodos; após o rollout, evitar
duas fontes de verdade. Períodos futuros coexistem com o vigente sem substituí-lo.

### Execução e idempotência

Adicionar a Schedule: execution_origin (`queue` por padrão ou `premium_direct`),
premium_period_id opcional, planned_duration_seconds, continued_from opcional único
e return_group_id opcional. ID de período registra a intenção do início; o relatório
ainda usa interseção temporal para atribuir tempo a renovações adjacentes.
Capturar o período vigente também em inícios pela fila, quando houver, para oferecer
continuidade premium ao concluir aquele item.

Invariantes: queue exige queue_item; premium_direct exige período e queue_item nulo.
Legado sem item recebe origem `legacy` durante migration de dados, sem forçar vínculo
inexistente. Uma execução aberta por escopo continua valendo para todas as origens.

POSTs de início/continuação exigem chave UUID de idempotência. Persistir escopo,
chave, hash do payload, referência de execução e resultado; mesma chave/payload
devolve a mesma execução, diferente payload retorna 409. Preservar tombstone se o
registro referenciado for removido: nunca recriar sessão por replay antigo.

Continuação exige antecessora concluída, mesmo jogo/período ainda vigente e versão
conhecida. A conclusão antecipada/reconciliação ocorre primeiro pelas rotas existentes;
depois criar a nova sessão sob lock da antecessora. Uma única sucessora de continuação
por execução impede duplicidade até entre dispositivos com UUIDs diferentes.
Iniciar manualmente mais tarde é uma nova ação, com nova chave, permitido.

### Fatos de horas e preferências

Estender GoalCompletion aditivamente com activity_id/name snapshot, started_at,
duration_seconds e execution_origin; completed_at já existe. Preservar os campos
e cálculos usados nas metas. Uma conclusão grava um fato, nunca um por período.
Não é necessário gravar agregados derivados em cada continuação.

Fatos novos possuem intervalo preciso. Fatos antigos podem ser enriquecidos uma
única vez via fonte Schedule/History, sem mudar valores antigos nem sobrepor dados
novos. Sem atividade/intervalo recuperável, manter as metas antigas e marcar cobertura
insuficiente para premium. Não distribuir minutos arbitrariamente entre dias.

GameplayTrackingSettings por scope_key: daily_reference_minutes (padrão 300),
grupos de gameplay e version. GET usa configuração padrão sem gravar. UI exige
seleção de grupos para participação no gameplay; horas do jogo podem ser vistas antes.
Respostas devolvem referência, grupos, fuso e versão usados nos cálculos.

### Revisão das consultas de fila

Auditar conjuntamente `group_reserved_minutes`, `category_started_count`,
`eligible_activities`, `activity_is_eligible`, `diagnose_empty_queue`, recriação/
reconciliação, Category.current_executions, Activity.can_execute/clean e o contador
de History.save. Excluir premium_direct das cotas; preservar queue e legacy.
Não excluir sessões diretas dos totais de metas e histórico real.

Separar serviço `premium_execution`, serviço/repository de períodos e repository
de relatórios. Reutilizar conclusão e fatos comuns. Evitar duplicar regras entre
views/Admin/jobs e não alterar silenciosamente os contratos de fila existentes.

## 7. API proposta para especificação de implementação

Todas as rotas novas exigem HasAPIKey. Escopo vem do servidor, nunca do JSON.
Catálogo de períodos é compartilhado; executions/history/summary/settings filtram
o escopo. Métodos GET são somente leitura. Listas paginadas (20, máximo 100).

| Rota | Contrato principal |
| --- | --- |
| GET/POST `/api/premium-periods/` | Listar/criar período; filtros activity_id e status |
| GET/PATCH `/api/premium-periods/<id>/` | Detalhe/edição permitida com expected_version |
| POST `/api/premium-periods/<id>/end/` | Encerrar antes, preservando datas originais; idempotente |
| POST `/api/premium-periods/<id>/start/` | duration_minutes, request_id, return_group_id opcional |
| POST `/api/activity-executions/<id>/continue/` | duration_minutes, request_id, expected_version |
| GET `/api/premium-periods/<id>/stats/` | Totais, dias, referência, cobertura, estimativa aberta |
| GET `/api/premium-analytics/summary/` | date_from/date_to, totais deduplicados e distribuição |
| GET `/api/premium-analytics/daily/` | Série diária no mesmo recorte |
| GET `/api/premium-analytics/history/` | Sessões paginadas; filtros activity_id, period_id, origin |
| GET/PATCH `/api/gameplay-tracking-settings/` | Referência e grupos; controle de versão |

Início novo retorna 201; replay retorna 200 com a execução canônica. Continuação
repetida da mesma antecessora devolve a sucessora existente quando payload corresponde;
payload conflitante retorna 409. Conflito com outra sessão retorna active_execution.
Antecessora estrangeira retorna 404; duração/data inválida 400; período não vigente,
edição bloqueada e versão desatualizada retornam 409 com códigos específicos.

Execução mantém todos os campos anteriores e acrescenta origin, planned_duration_seconds,
premium_period e return_group_id. IDs/contexto de fila são null em premium_direct;
fornecer activity_group_id/name sem fingir que é o grupo de uma fila.

Relatórios retornam segundos como inteiros, percentuais como decimais serializados
e null para base ausente. Incluir as_of, timezone, period_start/end_exclusive,
pending_reconciliation_count e coverage (complete/partial/legacy_inferred). Datas
de filtro finais são inclusivas no formulário e convertidas em fim exclusivo pelo
servidor. Limitar séries diárias a 366 dias por requisição; períodos maiores podem
ter total completo e histórico paginado, com gráfico consultado por janelas.

Antes de codificar, fechar exemplos JSON e fixtures de contrato compartilhadas
para período ativo, expirado, renovado, legado parcial e execução sem fila.

## 8. Migração, compatibilidade e rollout

1. Migrations aditivas para períodos, origem/duração/idempotência, fatos e preferências.
   Preencher origem queue/legacy conforme vínculo real; não converter toda ausência
   de queue_item em premium. Não executar carga grande dentro de migration.
2. Comando idempotente com dry-run importa as datas premium ainda disponíveis, inclusive
   expiradas com booleano desligado, marcando origem legacy_inferred. Não inferir
   pagamento só pelo booleano; pedir classificação Pago/Foco ao editar o catálogo.
3. Enriquecer fatos recuperáveis por lotes. Datas de assinaturas já sobrescritas não
   podem ser reconstruídas: relatar lacunas, sem inventar períodos anteriores.
4. Publicar backend compatível e captura de fatos, mantendo início direto atrás de
   flag de liberação. Validar dry-run/carga no ambiente de destino.
5. Publicar Flutter capaz de interpretar queue_item_id nulo, novos snapshots e
   decisão de continuidade em mobile/desktop. Só então habilitar início premium.
6. Rollback desabilita novos inícios diretos, preservando leitura/conclusão de sessões
   já abertas. Não reverter schema com dados nem distribuir cliente antigo para um
   escopo que ainda possui sessão direta aberta.

Esse cuidado é obrigatório porque o cliente atual falha no parsing de uma execução
sem item de fila, inclusive ao consultá-la depois de iniciar em outro dispositivo.

## 9. Sequência de entrega

1. **Backend — períodos e métricas:** migrations, importação de legado, snapshots
   temporais e contratos de consulta; compatibilidade com premium existente.
2. **Backend — execução direta:** início, continuação, conclusão comum, locks,
   idempotência e isolamento das cotas da fila; atualizar Postman.
3. **Flutter — execução:** modelos/snapshot, API/controller e timer único; Premium
   mobile/desktop com escolha de duração e decisão ao concluir.
4. **Flutter — acompanhamento:** cards, gráfico diário, histórico, períodos encerrados,
   renovação e configuração de grupos/referência.
5. **Integração e rollout:** validação em ambos os dispositivos e habilitação coordenada.

Complexidade: alta, pois muda a premissa “toda execução nasce de um item de fila” e
precisa preservar história temporal. Não exige implementar todas as rotinas da sugestão
3 nem pausa/retomada da sugestão 2. Pode aproveitá-las depois, mas a entrega é independente.

## 10. Critérios de aceite e testes

- Iniciar e repetir o mesmo premium três vezes no dia, mesmo com categoria no limite
  e referência de 5 h ultrapassada, sem consumir/reordenar a fila.
- Sessões diretas não aumentam contadores operacionais, mas aumentam horas reais e
  metas semanais exatamente uma vez. Grupo/categoria corretos mesmo após reclassificação.
- Concluir premium vindo da fila consome só seu item; continuar cria origem direta.
- Retornar a uma fila normal ou revisão preserva seu estado, respeitando invalidações
  legítimas. Não gerar eventos falsos de pulo ou favorito.
- Dois dispositivos tentando iniciar/continuar: só uma execução aberta e uma sucessora;
  testar timeout/replay, UUID repetido, payload divergente e chave de API diferente.
- Sessão encerrada cedo registra tempo parcial; espera após término não conta.
- Sessões atravessando meia-noite, fim de vigência e renovação têm recorte correto,
  inclusive segundos, sem duplicação de horas ou sessões no agregado.
- Período expirado continua consultável. Renovação não altera o anterior. Encerramento
  antecipado preserva limite contratado e registra limite efetivo.
- Referência compartilhada não se multiplica por quantidade de premiums; dias sem
  jogar entram na média; dia atual parcial é identificado; bases zero retornam null.
- Abrir relatório não conclui sessões; estimativa aberta é separada; reconciliação
  tardia usa horário efetivo, não o horário da consulta.
- Legado sem dados completos informa coverage; não apresenta zero como prova de que
  não houve gameplay quando a cobertura é desconhecida.
- Flutter: parse de execução antiga/nova, snapshot antigo, reinício, offline, troca
  de grupo, eventos atrasados, conclusão em outro dispositivo e notificações únicas.
- UI: larguras 360/720/1024/1440, textos longos, fonte ampliada, teclado e seleção sem
  início acidental. Mesmas ações de jogar/continuar/retornar em mobile e desktop.
- Backend: testes isolados SQLite e locks em PostgreSQL descartável. Flutter:
  analyze, testes de models/controllers/widgets e validação visual nas duas plataformas.

## 11. Estado desta task

Backend entregue com schema aditivo, APIs, comandos de legado, documentação, Postman
e testes isolados. `PREMIUM_DIRECT_START_ENABLED` permanece falso por padrão até o
Flutter aceitar `queue_item_id` nulo. Períodos importados recebem `legacy_inferred`
e exigem revisão humana de `paid`/`focus`; lacunas históricas permanecem declaradas
como cobertura parcial. Nenhum banco real ou arquivo do frontend foi alterado.

Custo por hora, telemetria Steam/jogo, cronômetro sem término, pausas, correção manual
de horas e rotinas por horário continuam fora do MVP.
