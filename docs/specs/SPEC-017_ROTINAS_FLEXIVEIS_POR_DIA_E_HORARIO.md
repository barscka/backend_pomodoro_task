# SPEC-017 — Rotinas flexíveis por dia e horário

Data: 2026-10-03. Status: proposta funcional para backend e Flutter; não implementada.

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

Oferecer futuramente “Usar rotina como referência” no acompanhamento Premium:
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

Modelo proposto, a detalhar em especificação de implementação:

- RoutinePlan: scope_key, timezone e identificação da rotina ativa.
- RoutineRevision: vigência prospectiva e versão para edição concorrente.
- RoutineBlock: dia(s), horários, deslocamento do fim, tipo (gameplay/cardio/família),
  perfil (geral/foco/interrompível) e referência estável para exceções.
- RoutineException: data de origem, bloco substituído/cancelado ou dia suspenso.
- ActivityRoutinePreference: atividade, scope_key e características conhecidas do
  modo de jogo; não expor preferências de outra chave de API.

API futura: CRUD versionado de plano/revisão, edição de exceções, agenda expandida
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

## 10. Critérios de aceite para a futura implementação

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
Task somente documental; fórmulas e coerência do plano verificadas, sem testes de
aplicação nem alteração de backend/Flutter executável.
