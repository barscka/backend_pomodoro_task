# Evolução do app — metas, pausas e rotinas

## Status e ordem de trabalho

Registrado em 2026-09-09. As três ideias foram selecionadas pelo usuário para trabalho
futuro. O backend da etapa 1 foi implementado em 2026-09-09. Em 2026-09-16, o usuário
confirmou a funcionalidade implementada e a leitura do Flutter confirmou telas e
integração de metas. As etapas 2 e 3 continuam como propostas futuras.

Nova evolução planejada em 2026-09-16: [Premium — escolha livre, continuidade e
acompanhamento por período](SPEC-014_PREMIUM_SESSOES_LIVRES_E_ACOMPANHAMENTO.md).
Permite escolher/repetir jogos premium sem depender da fila e acompanhar horas
dentro de cada vigência, com referência total de 5 horas diárias. Inclui backend
e fluxos Flutter mobile/desktop; não depende da implementação completa das rotinas.

Ordem acordada:

1. Metas semanais com acompanhamento de progresso.
2. Pausa e retomada, com intervalos configuráveis.
3. Rotinas por dia da semana e horário.

Os detalhes abaixo são propostas iniciais, a refinar ao iniciar cada funcionalidade.
A análise de origem foi limitada ao backend; conferir o frontend em
`/home/barscka/workspace/fullstack/frontend_pomodoro_task/` antes de fechar contratos e telas.

## Base existente

O backend possui atividades organizadas por categoria e grupo, limites diários,
filas persistentes, prioridade premium, registros de pulos, execução persistida e
histórico. Também oferece importação de atividades da Steam pelo admin.

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

## 3. Rotinas por dia da semana e horário

### Objetivo e MVP proposto

Adequar as sugestões à disponibilidade do usuário. Exemplos: estudo de segunda a
sexta das 19h às 22h e jogos aos fins de semana.

- Cadastrar janelas recorrentes de disponibilidade por dia e horário.
- Aplicar as janelas à elegibilidade das atividades na fila.
- Explicar indisponibilidade por rotina e mostrar a próxima janela disponível.
- Manter o comportamento atual para atividades sem restrição de rotina.

### Integração e critérios de aceite

- Integrar a regra aos serviços de fila, incluindo prévia, apresentação, início,
  recriação e revisão de itens pulados.
- Não iniciar uma atividade fora de sua janela permitida.
- Uma execução já iniciada não é interrompida quando a janela fecha.
- A exclusão temporária por horário não é registrada como pulo voluntário.
- A fila volta a oferecer atividades quando a janela abre, sem perder histórico.
- Cobrir limites de horário, virada de dia, fuso e interação com limites diários.

### Decisões pendentes e riscos

Definir se regras pertencem à atividade, categoria ou grupo e a precedência entre
elas. Definir janelas que atravessam a meia-noite, exceções por data e se basta
iniciar dentro da janela ou se toda a duração precisa caber nela.

Filas são persistentes: indisponibilidade temporária não pode eliminar uma atividade
definitivamente por reaproveitar, sem revisão, o mecanismo atual de expiração.
A prioridade premium deve respeitar as janelas ou ter uma exceção explicitamente
definida. Lembretes e integração com calendários ficam fora do MVP.

Estimativa qualitativa: complexidade média.

## Retomada do trabalho

A etapa 1 está implementada conforme atualização acima. A nova frente premium está
planejada na SPEC-014, ainda sem código. Pausas e rotinas gerais permanecem propostas
para trabalho posterior.

Para cada etapa, preservar os contratos existentes e validar regras de negócio e
API em banco de teste isolado, conforme os padrões pessoais Python API e de fluxo
de desenvolvimento. Validar o frontend no respectivo repositório.
