# SPEC-017 — Rotinas flexíveis por dia e horário

Data: 2026-10-03. Status: backend implementado e validado em ambiente isolado; Flutter e implantação pendentes.

## 1. Direção do produto

A rotina representa uma expectativa, não uma agenda obrigatória. Orienta a escolha
do jogo, mostra quanto tempo cabe agora e mantém visíveis os horários de treino e
atenção à família. Não proíbe jogar, não encerra partidas e não transforma tempo
livre em dívida de produtividade.

Esta proposta substitui a premissa anterior do roadmap de impedir início fora de
janela. Rotinas influenciam recomendações e apresentação; permissões reais de início
continuam obedecendo às regras existentes de fila, vigência premium e execução única.

Separar três conceitos na interface:

- **Disponibilidade planejada:** espaço reservado para jogar, incluindo interrupções.
- **Tempo registrado:** sessões efetivamente registradas pelo usuário no app.
- **Adequação ao momento:** jogo compatível com o tempo e a possibilidade de interrupção.

Registrar apenas a preferência operacional “sujeito a interrupções / atenção em casa”.
Não é necessário armazenar condição de saúde, motivo dos cuidados ou dados de terceiros.

## 2. Rotina inicial fornecida pelo usuário

### Segunda a sexta

| Horário | Bloco | Tratamento proposto |
| --- | --- | --- |
| 18:00–18:30 | Cardio | Reserva de 30 min, expectativa de treino de 15–30 min |
| 18:30–20:00 | Gameplay | 1h30 de disponibilidade |
| 20:00–22:30 | Tempo em família, jantar e assistir | 2h30 reservadas; sem sugestão proativa de jogo |
| 22:30–01:00 do dia seguinte | Gameplay | 2h30 de disponibilidade |

Se o cardio durar 15 min, o restante da reserva continua livre; não antecipar ou
ampliar o bloco de jogo automaticamente. Horários anteriores às 18h não foram
definidos nesta rotina e permanecem sem planejamento no app.

### Sábado e domingo

| Horário | Bloco | Tratamento proposto |
| --- | --- | --- |
| 07:00–09:00 | Gameplay focado | 2h, preferência por sessão de foco |
| 09:00–12:00 | Gameplay casual, sujeito a interrupções | 3h de janela; priorizar jogos pausáveis e solo; não recomendar online/grupo/competitivo |
| 12:00–13:00 | Não definido | Livre; não presumir disponibilidade para jogar |
| 13:00–14:30 | Almoço e tempo em família | 1h30 reservada |
| 14:30–18:00 | Gameplay | 3h30 de disponibilidade |
| 18:00–18:30 | Cardio | 30 min reservados |
| 18:30–19:00 | Não definido | Livre; não presumir disponibilidade para jogar |
| 19:00–22:30 | Tempo em família, jantar e assistir | 3h30 reservadas |
| 22:30–01:00 do dia seguinte | Gameplay | 2h30 de disponibilidade |

Não atribuir perfil focado ou competitivo aos blocos genéricos sem escolha do usuário.
Nenhum horário de sono ou trabalho será criado a partir de suposições.

### Capacidade nominal

| Medida | Valor |
| --- | --- |
| Gameplay por dia útil | 4h |
| Gameplay por dia de fim de semana | 11h, incluindo 3h sujeitas a interrupções |
| Gameplay segunda a sexta | 20h |
| Gameplay sábado e domingo | 22h |
| Disponibilidade semanal total | 42h |
| Parcela semanal sujeita a interrupções | 6h |
| Demais janelas de gameplay | 36h; não são garantia de jogo contínuo |
| Tempo em família planejado por dia útil | 2h30 |
| Tempo em família planejado por dia de fim de semana | 5h |
| Tempo em família planejado na semana | 22h30 |

42h/semana equivale a 6h/dia na média aritmética, mas esconderia a diferença entre
4h nos dias úteis e 11h no fim de semana. Exibir os dias separadamente. A referência
anterior de 5h/dia não deve ser substituída silenciosamente por esses números.
Não calcular uma taxa arbitrária de aproveitamento das janelas interrompíveis.

## 3. Comportamento no dia a dia

### Agora e próximo bloco

Home mostra “Agora”, “Até que horas” e “Próximo bloco”. Exemplos:

- Terça, 19:10: “Gameplay · 50 min disponíveis · às 20h, tempo em família”.
- Sábado, 10:00: “Gameplay casual · sujeito a interrupções · até 12h”.
- Terça, 20:30: “Tempo em família · próxima janela de jogo às 22h30”.
- Domingo, 12:15: “Horário livre, sem bloco definido · almoço às 13h”.

Oferecer durações que caibam no tempo restante, preservando o padrão da atividade
como alternativa. Se faltarem 20 min e o bloco padrão for 60, sugerir 20 em uma
execução direta que aceite essa duração, sem modificar Activity.duration.
Uma sessão de fila continua com a duração e elegibilidade próprias; não alterar
seu contrato nesta primeira entrega apenas para fazê-la caber na rotina.

### Liberdade e mudanças do dia

- Escolher um jogo fora da recomendação continua permitido pelas regras normais.
- Se uma sessão ultrapassar o próximo compromisso, mostrar o horário previsto de
  término e um aviso discreto; não exigir confirmação adicional para cada início.
- Continuar depois do limite é possível, sem encerrar, pausar ou trocar jogo sozinho.
- No fim de um bloco, atualizar a indicação visual. Notificação opcional, desligável,
  sem alertas repetidos ou obrigação de marcar o compromisso como cumprido.
- Permitir mover, encurtar, cancelar ou substituir um bloco **só hoje**. Mudança da
  rotina semanal é uma ação separada, com efeito prospectivo.
- “Hoje não vou seguir a rotina” suspende orientações do dia, preservando histórico.
  Um dia suspenso não vira automaticamente um dia inteiro disponível para gameplay.

Não inferir atenção real à família pela ausência de jogo. Os blocos familiares são
planejamento; o app não mede presença, cuidado ou qualidade desse tempo.

## 4. Recomendações adequadas à janela

### Perfil do jogo ou modo de jogo

Adicionar preferências opcionais por atividade: pausa imediata, uso online,
necessidade de grupo, competitivo, adequado a foco e duração mínima útil sugerida.
Usar valores explícitos ou desconhecido; não deduzir que todo retrô é pausável ou
que todo jogo com modo online exige esse modo em cada sessão.

No MVP, o perfil representa o modo que o usuário costuma jogar. Se houver modos
muito diferentes, avisar que a classificação vale para o modo escolhido; perfis
múltiplos por jogo podem ser uma evolução. Não classificar títulos automaticamente
com informações inventadas nem depender de fontes externas para funcionar.

### Janela casual de 09h–12h

Para a lista “Recomendados agora”, exigir confirmação de que pode interromper/pausar,
é solo e não é online nem competitivo. Perfil desconhecido fica como “Classificação
pendente”, fora dessa lista, mas acessível no catálogo geral.

Um Premium pago não supera a adequação ao momento. Por exemplo, um jogo online
vigente continua disponível para escolha manual, mas não ganha recomendação nessa
janela só por estar perto do vencimento. O objetivo é reduzir atrito ao precisar parar.

### Ordem sugerida

1. Compatibilidade com o tipo da janela e interrupções.
2. Duração da sessão que cabe antes do próximo bloco.
3. Preferências já existentes: Premium vigente, andamento retrô e contexto da fila.

Mostrar motivos concretos: “Pausável e solo”, “Cabe em 25 min”, “Premium termina em
3 dias”. Não criar uma nota opaca de produtividade nem uma obrigação de zerar jogos.

## 5. Integração com fila, Premium, RetroGames e pausas

- A rotina não altera ordem, estado ou identidade de itens na fila. Não pula/expira
  atividades incompatíveis e não dispara recriação automática.
- Exibir o próximo item da fila com sua adequação; uma sugestão alternativa não
  substitui silenciosamente o item já apresentado.
- Para Premium/RetroGames, recomendar entradas elegíveis e iniciar pelas rotas
  diretas existentes. Fora de janela continua valendo a liberdade de escolha.
- Para atividade comum, direcionar ao fluxo de fila/grupo existente. O MVP não cria
  uma terceira forma de início direto só para contornar a fila.
- Uma única execução permanece válida por escopo, independentemente de tela/origem.
- Horários familiares não são “atividade pulada” nem eventos negativos para metas.
- A primeira entrega pode funcionar sem pausa do timer: recomenda jogos que possam
  ser interrompidos, mas não alega descontar interrupções automaticamente.

Pausa/retomada é uma evolução importante para a precisão da janela casual. Até que
seja implementada, permitir encerrar cedo e iniciar novo bloco após a interrupção,
mantendo claro que o timer continua contando se não for encerrado. Não confundir
pausar o jogo no emulador com pausar a sessão do app.

## 6. Acompanhamento: planejado e registrado

Tela diária/semanal mostra janelas de gameplay, janelas sujeitas a interrupções e
horas registradas. Não cobrar “horas faltantes” nem carregar saldo não utilizado.

Para sessões encerradas, cruzar intervalos com as janelas:

- tempo dentro de janela de jogo;
- tempo fora das janelas planejadas, com rótulo neutro;
- tempo em janela casual sujeito a interrupções;
- tempo sem cobertura histórica, quando faltarem timestamps precisos.

As 6h casuais semanais aparecem como disponibilidade interrompível, não como meta de
6h jogadas. Estimativa aberta aparece separada das horas confirmadas. Dias sem plano,
plano suspenso e gameplay zero são situações diferentes.

Disponível no backend por escolha explícita “Usar rotina como referência” no acompanhamento Premium:
comparar com a soma das janelas no intervalo, sem multiplicar pelo número de jogos.
Manter como padrão a referência atual de 5h/dia até escolha explícita. Para referência
por rotina, devolver separadamente capacidade geral e interrompível; não misturar
denominadores de referências distintas em um mesmo percentual.

Métricas familiares ficam limitadas ao tempo reservado. Não publicar percentuais
de “atenção dada” a partir de dados de gameplay.

## 7. Meia-noite, recorrência e mudanças

- Fuso inicial: America/Sao_Paulo, informado pelo servidor.
- 22h30–01h pertence ao dia em que o bloco começa; armazenar deslocamento do fim
  para o dia seguinte explicitamente. Domingo termina segunda às 01h.
- Para determinar “agora”, consultar também ocorrências iniciadas no dia anterior.
- Relatório diário civil divide minutos à meia-noite. Na agenda, o bloco mantém seu
  rótulo do dia de origem. Explicar a diferença e evitar duplicidade na semana.
- Intervalos com fim exclusivo: às 20h já começa o bloco familiar, não dois blocos.
- Validar sobreposição após expandir recorrência, incluindo domingo/segunda e
  exceções. Rejeitar conflitos de configuração com explicação; não somar capacidades
  sobrepostas nem resolver prioridades escondidas.
- Exceção por data prevalece sobre o bloco semanal correspondente. Cancelamento
  de uma ocorrência não cancela todas as semanas.
- Mudanças semanais criam revisão com data de vigência. Relatórios passados usam
  o plano vigente à época; não reescrever expectativa histórica ao editar hoje.
- Não retroagir a primeira rotina para meses anteriores: sem plano na época significa
  “Sem planejamento registrado”, não “Jogou fora do horário”.

## 8. Experiência Flutter proposta

### Mobile

Como a navegação já tem cinco destinos, não adicionar uma sexta aba. Acesso Rotina
pelo card Agora na Home e por ação de agenda. Tela Hoje com blocos em lista e próxima
transição; tela Semana para edição e visão geral. Ajustes de hoje em bottom sheet.
Cards Premium/Retro exibem adequação sem remover o catálogo geral.

### Desktop

Entrada Rotina no shell existente, com semana em grade e detalhe do dia selecionado.
Na Home e no timer, card compacto de contexto e horário previsto de término. Em
largura intermediária, usar lista diária em vez de encolher sete colunas ilegíveis.

Nos dois layouts: estados vazio/erro/offline, texto ampliado, rótulos além das cores
e horário do servidor. RotinaController consulta agenda/recomendações; PomodoroController
continua responsável por execução, timer, snapshot e notificações de sessão.

## 9. Direção técnica e entregas

Complemento de layout e seleção no bloco: ver seção 12, incluída após referência
visual e orientação adicionais do usuário. Essa seção detalha a composição da
agenda e substitui a grade de sete dias como apresentação inicial no desktop.

Modelo implementado no backend; decisões detalhadas no contrato da seção 13:

- RoutinePlan: scope_key, timezone e identificação da rotina ativa.
- RoutineRevision: vigência prospectiva e versão para edição concorrente.
- RoutineBlock: dia(s), horários, deslocamento do fim, tipo (gameplay/cardio/família),
  perfil (geral/foco/interrompível) e referência estável para exceções.
- RoutineException: data de origem, bloco substituído/cancelado ou dia suspenso.
- ActivityRoutinePreference: atividade, scope_key e características conhecidas do
  modo de jogo; não expor preferências de outra chave de API.

API backend: plano versionado de plano/revisão, edição de exceções, agenda expandida
por intervalo, contexto atual e leitura de adequação/recomendações. Calcular no
backend, com payloads comuns a mobile/desktop. GET não cria rotinas, expira itens,
conclui sessões nem altera fila. Reutilizar escopo por Authorization; não criar login.

Ordem proposta:

1. **Agenda flexível:** modelos/API, importar o modelo inicial por ação explícita,
   edição semanal, exceções e card Agora/mobile/desktop. Sem banco real nesta task.
2. **Adequação de jogos:** classificação manual, explicações e recomendações integradas
   aos fluxos de início existentes, sem mudar fila.
3. **Planejado versus registrado:** cruzamento temporal, referência opcional para
   Premium e tratamento de cobertura histórica.
4. **Precisão nas interrupções:** integrar pausa/retomada quando essa frente for
   implementada, contando só segmentos ativos; não tornar a agenda dependente dela.

Não implementar jobs/automação para forçar o cumprimento da agenda. Integração com
calendários externos e detecção automática do jogo continuam fora do escopo.

## 10. Critérios de aceite

- Modelo inicial produz 4h úteis/dia, 11h por dia de fim de semana e 42h semanais,
  com 6h explicitamente interrompíveis. Gaps de 12h–13h e 18h30–19h continuam livres.
- Às 19h10 de terça informa 50 min até a próxima transição; não altera duração da fila.
- Às 10h de sábado recomenda somente jogos classificados como compatíveis com
  interrupções e sem online/grupo/competitivo; escolha manual permanece acessível.
- Às 00h30 reconhece a janela iniciada na véspera. Domingo/segunda não duplica horas.
- Exceção de hoje não muda outras semanas; alteração futura não reescreve passado.
- Rotina não bloqueia início por horário, não termina sessão, não consome fila,
  não cria pulo e não contorna vigência Premium nem execução única.
- Comparativos distinguem disponibilidade, gameplay confirmado, estimativa aberta,
  rotina suspensa e ausência de plano; não inferem cuidado familiar realizado.
- Troca de dispositivo/escopo respeita versão e isolamento. Ações offline não são
  apresentadas como alteração já salva.
- Testes backend de expansão/recorte/exceções e contratos; testes Flutter de estado,
  telas pequenas, desktop, fonte ampliada e recuperação de rede.

## 11. Premissas desta proposta

Blocos não definidos ficam livres. Cardio útil reserva até 18h30 e permite 15–30 min.
Janelas genéricas não significam foco intenso. Preferências restringem recomendação,
não escolha. O plano inicial será editável; nenhum horário foi cadastrado em banco.

Padrões usados: personal-python-api, personal-flutter e personal-dev-workflow.
O planejamento inicial foi documental. A implementação backend e suas validações
estão registradas na seção 13; Flutter não foi alterado.

## 12. Referência visual e escolha de atividade no bloco de gameplay

Atualização de 2026-10-03: o usuário forneceu referência visual de uma tela escura
com navegação lateral, dois quadros de rotina, contexto atual à direita e resumo
semanal abaixo. Solicitou escolher, no horário de gameplay, um Premium ou a próxima
atividade aleatória/de pulados dos grupos. Esse complemento iniciou como planejamento; o backend está entregue na seção 13.

### Composição desktop

- Reutilizar o shell e tema existentes; acrescentar Rotina sem remover destinos
  existentes nem reproduzir literalmente os nomes de menu ilustrativos da imagem.
- Cabeçalho com título, indicação de flexibilidade e ação Editar rotina.
- Área principal com quadros **Segunda a sexta** e **Sábado e domingo** lado a lado,
  contendo cartões de horário, ícone, título e perfil. Controle Seg–Sex/Sáb–Dom
  destaca o quadro escolhido; em largura menor, alterna um quadro visível por vez.
- Coluna lateral com **Hoje / Agora**, próximo bloco, Ajustar só hoje e Suspender
  orientações hoje. Logo abaixo, painel de escolha **O que jogar neste bloco?**.
- Resumo inferior: 42h disponíveis para gameplay, 22h30 familiares planejadas,
  6h interrompíveis, 4h por dia útil e 11h por dia de fim de semana.
- Em desktop intermediário, mover o painel de contexto/seleção para baixo dos
  quadros. Evitar três colunas estreitas e rolagem horizontal obrigatória.

Datas, barra de progresso, tempo restante e recomendações são dados reais do contexto;
os valores e a data ilustrativos da imagem não devem ser copiados para a interface.
A barra representa o avanço do bloco, não horas efetivamente jogadas.
Não marcar 18h30–20h como Foco automaticamente: o plano fornecido classifica esse
bloco como gameplay geral. Foco explícito permanece em 07h–09h no fim de semana.

### Escolha dentro do bloco

Ao selecionar um cartão de gameplay, exibir duas fontes:

| Fonte | Seleção e prévia | Ação |
| --- | --- | --- |
| Premium | Escolher período vigente; mostrar jogo, vigência, adequação e duração | Jogar este Premium |
| Fila do grupo | Escolher grupo; mostrar próxima atividade canônica e modo da fila | Jogar próxima atividade |

Na fonte Fila do grupo, o modo aparece como **Fila normal — ordem aleatória já
definida** ou **Revisão de pulados**, conforme a fila operacional daquele grupo.
“Próximo aleatório” significa a próxima posição persistida, não um novo sorteio ao
reabrir o seletor. Mostrar nome, grupo, duração, origem e compatibilidade com o bloco.

A revisão de pulados segue seu ciclo atual. Não inventar uma fila de revisão que
ainda não existe, permitir repular item bloqueado ou consumir uma fila encerrada.
Se o produto passar a exigir acesso independente aos pulados antes de terminar a
fila normal, tratar como mudança explícita de backend, fora deste complemento.

Exemplo de uso: terça às 18h30, selecionar o bloco e escolher entre um Premium vigente
e a próxima atividade do grupo Games. Ao concluir o Premium, Continuar mantém o
fluxo direto; Voltar à fila usa o grupo escolhido, preservando a fila existente.
No modo de revisão, a prévia identifica claramente que a próxima atividade foi pulada.

Separar **planejar a preferência** de **iniciar sessão**:

- A ocorrência pode guardar preferência por premium_period_id ou por group_id,
  nunca reservar um queue_item_id para um horário futuro.
- No MVP, essa escolha vale só para a ocorrência selecionada. Replicar semanalmente
  exige ação explícita; selecionar hoje não altera todas as terças.
- Selecionar card, fonte ou jogo não inicia o timer. Início sempre tem botão próprio.
- Ao abrir/iniciar, revalidar período, grupo, fila, execução ativa e adequação. Se o
  próximo item mudou, atualizar a prévia e explicar; não consumir a escolha antiga.
- Preferência vencida/inativa permanece visível como indisponível, oferecendo outra
  escolha, sem substituição silenciosa ou início agendado automático.
- Durante execução, manter visível o timer único. Não permitir que outra seleção
  crie sessão paralela; navegar na agenda continua permitido.

RetroGames permanece disponível no módulo próprio. Esta escolha rápida não cria
nova origem de execução: usa premium_direct ou o item real da fila selecionada.

### Janela casual e horários familiares

No bloco casual, classificar cada opção como recomendada, não adequada ao momento
ou com perfil desconhecido. Premium online não vira recomendado por ser Premium.
A próxima atividade da fila pode ser incompatível; mostrar essa informação sem
pulá-la automaticamente e oferecer escolher outra fonte/grupo.

Blocos de cardio/família mostram edição e contexto, sem seletor de jogo embutido.
Os módulos gerais continuam acessíveis, preservando a natureza orientativa da rotina.

### Mobile

Card Hoje / Agora no topo, alternância Seg–Sex/Sáb–Dom e lista de blocos. Tocar em
gameplay abre bottom sheet ou tela de detalhe com Premium/Fila do grupo, seleção,
prévia e botão de início. Resumo semanal em cards com quebra de linha. Não copiar
as três colunas do desktop nem acrescentar uma sexta aba à navegação atual.

### Contrato e testes adicionais necessários

- Preferência por ocorrência deve usar ID estável do bloco, data local de origem
  e versão; um bloco 22h30–01h mantém a data em que começou.
- A prévia de fila precisa ser somente leitura. Conferir o contrato existente de
  activities/next antes de reutilizá-lo, pois ele apresenta/cria fila. Reutilizar a
  listagem não mutante quando suficiente ou expor uma prévia específica, com
  estados fila ausente/vazia/revisão. Apresentar/criar fila apenas por ação explícita.
- Validar que abrir agenda, alternar grupos e salvar preferência não inicia sessão,
  apresenta/consome item, recria fila nem registra pulo.
- Cobrir troca do item entre prévia e início, Premium vencendo antes do bloco,
  revisão bloqueada, concorrência entre dispositivos, contexto de retorno e fonte
  incompatível com janela casual.


## 13. Entrega backend — 2026-10-03

Implementados plano por escopo, revisões prospectivas, blocos estáveis, exceções,
suspensão civil, agenda/contexto, classificação opcional, recomendação determinística,
seleção Premium/fila por ocorrência, prévia assinada somente leitura e início explícito
pelos serviços existentes. Resumo cruza sessões confirmadas com intervalos civis;
referência Premium por rotina é opt-in, sem substituir 300 minutos/dia por padrão.

Contrato fechado e decisões: [SPEC-017 API v1](../contracts/spec-017-routines.md).
Fixtures: [respostas versionadas](../contracts/spec-017-routines.json).
Postman atualizado com pasta Rotinas flexíveis (SPEC-017).
Migration aditiva 0021, sem carga de rotina. O modelo inicial só existe após POST
explícito; GETs não criam filas, não apresentam itens e não reconciliam execuções.

Versionamento global serializa revisão, ajuste, suspensão e seleção; classificação
usa versão própria por atividade/escopo. Primeira vigência começa hoje/futuro;
revisões seguintes começam amanhã ou após a última revisão futura. Não editamos
revisões publicadas. IDs estáveis podem ser mantidos nas revisões seguintes.
A suspensão cobre o dia civil inteiro, incluindo a madrugada da ocorrência anterior.
O resumo civil e a capacidade nominal por dia de origem são campos distintos.

Validações e comandos reproduzíveis no
[handoff da entrega](../handoffs/06_handoff_rotinas_flexiveis.md).
Testes incluem seção 12, consulta sem mutações, fila ausente/revisão, Premium
vencido, conflito de versão/prévia, madrugada e concorrência PostgreSQL descartável.
Nenhum banco real, migration real, carga editorial ou flag de ambiente foi alterado.

Ainda não entregues: telas/integração Flutter, deploy, pausa/retomada, automações,
notificações de transição e calendários externos. Seleção não reserva item futuro.
Uma atividade incompatível continua iniciável pelas regras canônicas; a rotina
não encerra, bloqueia por horário nem altera duração padrão da fila.

## 14. Associação automática de sessões — 2026-10-04

Inícios queue (grupos explicitamente configurados/período Premium), premium_direct
 e retro_direct agora associam a sessão ao bloco de gameplay ativo em starts_at.
A data de origem sobrevive à madrugada, retries e ultrapassagem do horário final.
Sem janela ativa, a sessão segue normal sem vínculo. Estudos/trabalho não são
classificados como gameplay pelo nome ou pela tela Foco.

Migration aditiva 0022, snapshot histórico por execução, sem backfill nem banco real.
Agenda retorna sessões por ocorrência e recorded_occurrences para manter registros
quando o planejamento foi ajustado/cancelado. Tempo confirmado e estimativa aberta
são distintos; o resumo civil permanece independente, sem duplicação.

Contrato aditivo e fixtures em docs/contracts/spec-017-routines.md e
spec-017-routine-sessions.json. Evidências, riscos e integração Flutter no
[handoff da associação](../handoffs/07_handoff_sessoes_na_agenda.md).
