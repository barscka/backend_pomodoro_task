# Evolução do app — metas, Premium, RetroGames e próximas frentes

## Estado atual — revisão de 2026-10-03

Roadmap original registrado em 2026-09-09, atualizado após leitura do código,
contratos, testes existentes e documentos dos dois projetos. Referências da revisão:
backend `d0c0f2d` em `main`; Flutter `d4b33b5` em
`feature/ui-redesign-consistency`. “Implementado” abaixo significa presente nesses
checkouts; não comprova publicação, migration aplicada ou flag habilitada no ambiente real.

| Frente | Backend | Flutter | Situação |
| --- | --- | --- | --- |
| Metas semanais | Implementado, incluindo sinais de pulos | Implementado | Entrega existente; SPEC-BACK-012/013 |
| Premium: sessões livres e acompanhamento | Implementado | Implementado, mobile e desktop | Substitui o status antigo de apenas planejamento da SPEC-014; conferir liberação no ambiente |
| RetroGames: catálogo, progresso e sessões | Implementado | Implementado, mobile e desktop | Nova frente entregue; SPEC-BACK-015 e SPEC-FRONT-010 |
| Importação versionada do catálogo RetroGames | Implementado | Consome o catálogo pela API existente | SPEC-BACK-016; carga no ambiente não verificada nesta revisão |
| Shell e experiência desktop | Sem nova regra de domínio | Implementado no checkout analisado | SPEC-FRONT-012 marcada como homologada no frontend |
| Nova experiência mobile | Sem nova regra de domínio | Especificação em draft | SPEC-FRONT-013; não confundir com a tela Retro mobile já existente |
| Pausa e retomada com intervalos | Proposta futura | Proposta futura | Item 2 original |
| Rotinas flexíveis por dia/horário | Planejamento funcional | Planejamento mobile/desktop | SPEC-017, frente escolhida para detalhamento |

A ordem original era metas → pausas → rotinas. Premium e RetroGames foram entregues
como evoluções adicionais; isso não torna pausas ou rotinas automaticamente prontas.
Em 2026-10-03, o usuário escolheu detalhar rotinas com sua expectativa semanal.
A SPEC-017 registra esse plano flexível; a codificação ainda não foi solicitada.

Os detalhes dos itens 1–3 abaixo preservam o planejamento original. Para metas,
prevalecem as decisões já implementadas nas SPEC-BACK-012/013; as perguntas antigas
não significam que o MVP continua pendente.

## Base existente

O backend possui atividades organizadas por categoria e grupo, limites diários,
filas persistentes, prioridade premium, registros de pulos, execução persistida e
histórico. Também oferece importação de atividades da Steam pelo admin, períodos
Premium, sessões diretas Premium/RetroGames e catálogo retrô versionado.

O Flutter possui os módulos correspondentes integrados ao mesmo coordenador de
execução. No mobile, Retro é um destino próprio; no desktop, RetroGames participa
do shell persistente junto de Pomodoro, Premium, histórico, fila e metas.

Principais pontos de integração:

- `apps/pomodoro/models.py`: Group, Category, Activity, Schedule e History.
- `apps/pomodoro/services/activity_execution.py`: ciclo de execução.
- `apps/pomodoro/services/activity_queue.py`: elegibilidade e seleção da fila.
- `apps/pomodoro/views.py` e `serializers.py`: contratos HTTP existentes.

## 1. Metas semanais com acompanhamento de progresso

Planejamento do backend: [SPEC-BACK-012 — Metas semanais](SPEC-BACK-012_METAS_SEMANAIS.md),
elaborado e implementado em 2026-09-09. Essa especificação detalha as
decisões do backend do MVP e substitui as questões em aberto abaixo onde houver
uma decisão explícita.

### Objetivo e experiência

Transformar o histórico em acompanhamento de objetivos pessoais. Exemplos:
“estudar 240 minutos por semana” e “concluir 3 sessões de leitura por semana”.
O usuário cadastra uma meta para um grupo ou categoria e consulta o realizado,
o restante e o percentual atingido na semana.

### MVP proposto

- Criar, listar, editar e desativar metas semanais.
- Escolher uma métrica: minutos concluídos ou quantidade de sessões concluídas.
- Vincular cada meta a um grupo ou a uma categoria, com alvo positivo.
- Exibir alvo, realizado, restante e percentual, além das datas do período.
- Mostrar metas atingidas sem impedir novas sessões ou limitar a fila.
- Apresentar o progresso em uma tela de metas no frontend.

Metas são objetivos; os limites diários existentes continuam sendo restrições de
execução. As duas regras devem coexistir de forma explícita na interface.

### Regras propostas para discussão técnica

- Semana de segunda-feira a domingo no fuso adotado pelo app; consultas usam início
  inclusivo e início da semana seguinte exclusivo.
- Contabilizar somente execuções concluídas. History é criado no início da sessão,
  portanto a existência do registro não comprova conclusão.
- Usar o instante de conclusão para atribuir a sessão à semana, inclusive quando ela
  atravessa a meia-noite ou a virada da semana.
- Usar a duração registrada da execução concluída, evitando recalcular o passado
  pela duração atual de Activity.
- Garantir que reconciliações ou requisições repetidas não dupliquem progresso.
- O restante nunca fica negativo; permitir indicar realização acima de 100%.
- Metas sobrepostas podem acompanhar a mesma sessão, mas uma sessão não pode ser
  duplicada dentro da agregação de uma mesma meta.

### Caminho de implementação

1. Conferir telas e consumo do histórico no frontend; revisar a semântica dos campos
   de duração e conclusão e o mecanismo atual de escopo das execuções.
2. Fechar decisões pendentes e escrever uma especificação de implementação com modelo,
   migration aditiva, contratos HTTP e exemplos de resposta.
3. Persistir as metas e concentrar cálculo de progresso em serviço de domínio,
   mantendo views e serializers focados no contrato.
4. Implementar testes isolados de serviço e API e entregar o backend.
5. Integrar cadastro e visualização de progresso no frontend e validar o fluxo completo.

### Critérios de aceite

- Uma meta de 240 minutos com duas sessões concluídas de 60 minutos informa
  120 realizados, 120 restantes e 50% de progresso.
- Uma meta de 3 sessões com 3 conclusões aparece atingida.
- Sessões abertas, canceladas e expiradas não aumentam o progresso.
- Sessões de outro escopo ou fora do grupo/categoria da meta não são contabilizadas.
- A virada da semana e o fuso são cobertos por testes, sem alterar o período anterior.
- Consultar progresso não altera histórico, fila ou contadores de execução.
- Alterações na duração de uma atividade não reescrevem minutos já realizados.

### Decisões pendentes e riscos

- Definir identidade/propriedade das metas e isolamento; não presumir que o escopo
  atual equivale a uma conta de usuário estável.
- Definir se uma nova meta inclui sessões anteriores da semana em andamento.
- Definir quando uma edição de alvo passa a valer e como preservar metas anteriores.
- Definir o tratamento histórico de atividades movidas entre categorias ou grupos,
  e de exclusões que afetem os registros usados no cálculo.
- Conferir qualidade dos históricos antigos e a regra para durações ausentes.
- Confirmar a semântica do grupo agregador Todos e a política de metas duplicadas.

Comparação entre semanas, gráficos de distribuição do tempo e notificações de meta
ficam para uma evolução após o MVP. Estimativa qualitativa: complexidade média.

## 2. Pausa e retomada, com intervalos configuráveis

### Objetivo e MVP proposto

Permitir interrupções sem perder o tempo restante e organizar os descansos entre
sessões. Exemplo: pausar uma atividade de 25 minutos após 10 minutos e retomá-la
com 15 minutos restantes.

- Pausar e retomar uma execução ativa, persistindo o estado no backend.
- Recuperar o estado correto ao reabrir o app ou trocar de dispositivo.
- Configurar descanso curto, descanso longo e número de ciclos até o descanso longo.
- Exibir claramente se o contador corresponde à atividade ou ao descanso.
- Propor início manual da próxima atividade após o descanso no primeiro MVP.

### Integração e critérios de aceite

- Estender o ciclo de Schedule e os contratos de execução, revisando constraints,
  reconciliação temporal e compatibilidade dos clientes.
- Pausa não consome minutos de atividade nem permite abrir uma execução conflitante.
- Retomada recalcula o término previsto usando o saldo persistido.
- Comandos repetidos ou concorrentes não duplicam pausas, ciclos ou conclusões.
- Descansos não contam para metas de foco e não consomem itens da fila.
- Cobrir pausa perto do término, múltiplas pausas, reabertura do app e reconciliação.

### Decisões pendentes e riscos

Definir expiração de sessões pausadas, cancelamento, pulo do descanso, persistência
das preferências e comportamento ao mudar de grupo. Revisar impacto em orçamento
diário, duração histórica e metas da etapa 1. A contagem deve ser autoritativa no
backend, sem depender apenas do relógio do cliente.

Estimativa qualitativa: complexidade média a alta.

## 3. Rotinas flexíveis por dia da semana e horário

Plano atual: [SPEC-017 — Rotinas flexíveis](SPEC-017_ROTINAS_FLEXIVEIS_POR_DIA_E_HORARIO.md),
elaborado em 2026-10-03 a partir da expectativa semanal fornecida pelo usuário.
Status: planejamento funcional para backend e Flutter, sem implementação.

Referência visual incorporada à seção 12 da SPEC-017: quadros de dias úteis/fim de
semana, painel Hoje/Agora e seleção no bloco de gameplay entre Premium e próxima
atividade da fila normal/revisão de pulados do grupo, sem avançar a fila na prévia.

A rotina orienta escolhas e apresenta o próximo compromisso; não bloqueia início,
não encerra partidas nem aplica penalidades. Essa decisão substitui explicitamente
as restrições de elegibilidade por horário propostas na versão inicial do roadmap.

- Dias úteis: cardio 18h–18h30 (treino de 15–30 min), gameplay 18h30–20h,
  tempo em família 20h–22h30 e gameplay 22h30–01h.
- Fim de semana: gameplay focado 07h–09h, casual/interrompível 09h–12h,
  almoço/família 13h–14h30, gameplay 14h30–18h, cardio 18h–18h30,
  família 19h–22h30 e gameplay 22h30–01h. Intervalos não definidos ficam livres.
- Disponibilidade nominal: 4h por dia útil, 11h por dia de fim de semana,
  total de 42h semanais; 6h são explicitamente sujeitas a interrupções.
- Janela casual recomenda jogos pausáveis e solo, sem online/grupo/competitivo;
  perfil desconhecido não é presumido adequado. Catálogo geral permanece acessível.
- Registrar planejado versus gameplay registrado, sem tratar disponibilidade como
  meta obrigatória nem inferir atenção real à família a partir do timer.
- Suportar ajustes só hoje, dia suspenso, revisão semanal e blocos atravessando
  meia-noite. Não reescrever o planejamento passado após edição.
- Preservar fila, Premium e RetroGames. Adequação temporal é contexto e recomendação,
  sem criar outro timer ou expirar/pular itens automaticamente.

Entrega proposta: agenda/contexto atual → adequação de jogos → comparação temporal
com histórico → integração futura de pausa/retomada para descontar interrupções.
A referência Premium de 5h/dia só muda para capacidade da rotina por escolha explícita.

## 4. Premium — implementação existente

Referência: [SPEC-014](SPEC-014_PREMIUM_SESSOES_LIVRES_E_ACOMPANHAMENTO.md).

Backend com períodos, início direto, continuação, acompanhamento e configurações
de gameplay. Flutter com modelos, PremiumController, PremiumScreen, formulários e
integração ao timer canônico. O status anterior deste roadmap (“ainda sem código”)
não corresponde mais ao estado dos repositórios.

A flag `PREMIUM_DIRECT_START_ENABLED` permanece `False` como padrão do código.
Isso não indica o valor efetivo no deploy. Compatibilidade dos clientes e liberação
no ambiente devem ser verificadas operacionalmente, sem reimplementar a feature.
O cabeçalho histórico da SPEC-014 ainda menciona Flutter pendente; para o panorama
atual, considerar esta revisão e o código consultado.

## 5. RetroGames — catálogo, progresso e sessões implementados

Referências:

- [SPEC-BACK-015 — Catálogo e sessões](SPEC-BACK-015_CATALOGO_E_SESSOES_RETROGAMES.md).
- [Contrato versionado para o frontend](../contracts/spec-015-retrogames.json).
- [Handoff do backend](../handoffs/04_handoff_spec_back_015_retrogames.md).
- [SPEC-FRONT-010 — Menu e fluxo RetroGames](/home/barscka/workspace/fullstack/frontend_pomodoro_task/docs/specs/SPEC-FRONT-010_MENU_E_FLUXO_RETROGAMES.md).
- [Handoff do Flutter](/home/barscka/workspace/fullstack/frontend_pomodoro_task/docs/handoffs/SPEC_FRONT_010_RETROGAMES_IMPLEMENTADA.md).

### Entrega no backend

- Catálogo organizado por grupo Retrogames → geração → plataforma → jogo, com
  ordem editorial, ano quando disponível, classificação essencial/complementar,
  objetivo de jogo, estimativa total e duração padrão do bloco.
- Modelos RetroPlatform, RetroGame e RetroGameProgress; jogo vinculado a Activity,
  preservando a infraestrutura de execução, histórico e metas.
- Consulta de gerações, plataformas, jogos, detalhe e sessões. Jogos têm busca,
  filtros por geração/plataforma/tier/status/atividade e paginação.
- Progresso por escopo: não iniciado (derivado), em andamento, concluído ou pulado,
  com versão e transições explícitas. Tempo estimado não conclui um jogo sozinho;
  jogar novamente não reabre automaticamente um concluído/pulado.
- Início `retro_direct` e continuação com duração de 1 a 720 minutos por bloco,
  idempotência e uma execução aberta por escopo, compartilhada com Pomodoro/Premium.
- Sessões retrô não criam nem consomem itens de fila ou períodos Premium. Cotas da
  fila consideram origens queue/legacy; execução direta continua entrando no histórico
  e nos fatos de conclusão para metas.
- Horas consolidadas e estimativa da sessão aberta separadas, com cobertura parcial
  identificada quando o legado não tem precisão de segundos.
- Migration aditiva `0020`, cadastro editorial no Admin e rotas registradas em urls.py.

### Entrega no Flutter

- Modelos e transporte RetroGamesApi integrados ao contrato real; RetroGamesController
  cuida de busca, filtros, seleção, paginação, carregamento, erros e conteúdo desatualizado.
- PomodoroController continua dono do timer, notificações, início/continuação e
  reconciliação. Não há um cronômetro retrô independente.
- Snapshots e ActivityExecution reconhecem `retro_direct` e `retro_game_id`, sem
  fabricar item de fila, com recuperação da execução e retries idempotentes.
- Mobile com destino **Retro** na navegação; desktop com **RetroGames** no shell
  compartilhado e composição responsiva de catálogo, filtros e detalhe.
- Detalhe mostra objetivo, estimativa total, próximo bloco, tempo jogado e sessões
  recentes. Permite jogar, marcar concluído, pular e retomar o planejamento.
- Após uma sessão: continuar no jogo, marcar o jogo concluído ou voltar ao catálogo.
  Concluir um bloco e concluir o jogo são ações distintas.

### Limites e acompanhamento operacional

- `RETROGAMES_ENABLED=False` é o padrão do código; leitura do catálogo continua
  disponível. Valor real da flag e distribuição dos clientes não foram inspecionados.
- O handoff original do Flutter registrava homologação integrada pendente. Uma nota
  posterior registra consulta à API local com 116 jogos e suporte a ano nulo; isso
  confirma parte da integração, não a homologação completa de todos os fluxos.
- Confirmar no ambiente de destino migration, catálogo importado, início, conclusão
  antecipada, continuação, reinício e conflito entre dispositivos com a mesma chave.
- Testes de concorrência PostgreSQL existem no backend; o handoff inicial registra
  que não foram executados naquela entrega. Não tratá-los como aprovados sem nova evidência.
- Não inclui ROMs, emuladores, abertura automática de jogos ou detecção de gameplay.
  As horas vêm de sessões registradas no app.

## 6. Importação do catálogo RetroGames — implementada

Referência: [SPEC-BACK-016](SPEC-BACK-016_IMPORTACAO_CATALOGO_RETROGAMES.md) e
[handoff da importação](../handoffs/05_handoff_importacao_catalogo_retrogames.md).

Catálogo em `apps/pomodoro/data/retrogames_catalog.json`, com schema/versão editorial:
**8 gerações, 23 plataformas e 116 jogos**, totalizando **60.060 minutos (1001 h)**
de estimativa. Contagens e soma recalculadas diretamente do JSON nesta revisão.
A fonte declarava 991 h; a divergência está documentada e o importador usa a soma
das linhas. Essas horas são estimativas do roteiro, não horas já jogadas.

Entregue:

- Validação estrutural, chaves estáveis e identidade externa por jogo; títulos iguais
  em plataformas distintas não são confundidos.
- Comando `import_retrogames_catalog` com dry-run, relatório humano/JSON, carga
  transacional e idempotente e comparação opcional com inventário local.
- Atualização de campos editoriais sem administrar progresso, sessões, fatos, filas
  ou configuração Premium.
- Itens ausentes são relatados como órfãos; `--deactivate-missing` desativa somente
  jogos/atividades comprovadamente gerenciados pelo catálogo, sem apagar histórico.

Limites: plataformas órfãs não são desativadas automaticamente por falta de proveniência;
comparação de inventário é opcional e não acompanha o deploy. O catálogo já pode ser
carregado em lote — cadastro manual exclusivo, mencionado nos handoffs iniciais de
RetroGames, foi superado por esta entrega. A presença do JSON não comprova carga no
banco usado pelo usuário.

## Retomada do trabalho

1. **Consolidar o que já foi entregue:** conferir ambiente e fluxos integrados de
   Premium/RetroGames e revisar lacunas de homologação documentadas. Não refazer
   catálogo, sessões ou importador como se ainda fossem propostas.
2. **Manter a curadoria:** atualizar o JSON versionado quando necessário, revisar
   dry-run e preservar identidade externa e progresso ao publicar novas versões.
3. **Detalhar a implementação de rotinas:** a SPEC-017 agora define a frente escolhida
   pelo usuário. Próximo passo é fechar modelos, contratos e testes da agenda flexível.
   Shell mobile e pausas continuam frentes separadas; pausa será útil para medir
   interrupções, mas não é pré-requisito para a agenda.

Pausas futuras devem contemplar queue, premium_direct e retro_direct, sem contabilizar
intervalos nas horas jogadas. A SPEC-017 define rotinas como
orientação para recomendações, preservando a liberdade de escolha direta.

## Evidências e validação desta atualização

Análise estática de modelos, serviços, rotas, controllers, telas, navegação, testes e
handoffs. Padrões carregados: personal-python-api, personal-flutter e personal-dev-workflow.
Doctors dos dois projetos passaram, com aviso apenas da variável PERSONAL_SKILLS_HOME
não definida (o local_path configurado resolve o repositório).

Validado JSON do catálogo, contagens/soma e links documentais. Suites de backend e
Flutter não foram reexecutadas por se tratar de atualização documental; resultados
citados nos handoffs pertencem às respectivas entregas. Não houve consulta ao banco,
importação de catálogo, mudança de flags nem alteração no frontend.
