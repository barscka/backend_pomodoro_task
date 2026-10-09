---
spec_id: SPEC-018
titulo: Catálogo de filmes e roleta do Oscar
status: PROPOSED
fase: PLANEJAMENTO
criado_em: 2026-10-09
---

# SPEC-018 — Catálogo de filmes e roleta do Oscar

## 1. Objetivo e experiência

Adicionar um menu **Filmes**, inicialmente com a coleção **Oscar — Melhor Filme**, para sortear um filme ainda não assistido, acompanhar a escolha atual e completar a coleção ao longo do tempo.

Fluxo principal:

1. Cadastrar Activity com o nome do filme, categoria Oscar, grupo Entretenimento e metadados cinematográficos.
2. Abrir Filmes e ver progresso, filmes disponíveis e eventual escolha pendente.
3. Clicar em **Girar roleta**. A animação desacelera durante 5 a 10 segundos.
4. Exibir título, ano de lançamento, ano da premiação, pôster e ações.
5. **Começar a assistir** registra intenção de assistir; **Marcar como assistido** registra conclusão explicitamente.
6. O filme concluído sai dos próximos sorteios daquele usuário/escopo. Ao acabar, mostrar coleção concluída e histórico.

O vídeo local foi usado como referência de um desafio de assistir vencedores; foram inspecionados quadros aos 3 e 45 segundos. Não foi realizada análise integral do vídeo nem transcrição. A quantidade exibida no vídeo não define o tamanho do catálogo do app. Esta spec não fornece nem valida uma lista histórica de vencedores.

## 2. Base existente verificada

Backend Django/DRF:

- `apps/pomodoro/models.py`: Group → Category → Activity; Activity tem nome, descrição, duração padrão, active e identidade externa. Não possui ano nem status de filme assistido.
- RetroGame usa extensão OneToOne de Activity e RetroGameProgress guarda progresso por scope_key. É o precedente para este desenho.
- `apps/pomodoro/urls.py` usa routers DRF. Views usam HasAPIKey e identidade lógica obtida por build_scope_key(request).
- Já existem services e repositories. Regra de sorteio deve ficar fora de views e serializers.
- Filas, Schedule, History e GoalCompletion têm regras próprias. O sorteio não é uma execução Pomodoro.
- Migrações existentes chegam a 0022; criar migração aditiva com a próxima numeração disponível na implementação.

Frontend Flutter:

- `lib/controllers/retrogames_controller.dart` utiliza ChangeNotifier; modelos e interfaces de API são explícitos.
- `lib/services/api_service.dart` concentra o cliente HTTP compartilhado.
- `lib/widgets/common/desktop_app_shell.dart` define DesktopSection.
- `lib/screens/home_desktop.dart` e `home_mobile.dart` compõem navegação; já há tela específica de RetroGames.
- Reutilizar tema, tokens, estados e padrões do projeto. Não trocar gerenciamento de estado.

Não acessar banco real nem supor que um ID local específico identifica Entretenimento. Vincular IDs selecionados no admin; nomes são rótulos editáveis.

## 3. Decisões de produto

### 3.1 Separar catálogo de progresso

Não adicionar `watched` global a Activity: isso misturaria metadados compartilhados com progresso pessoal. Manter Activity como identidade/nome e criar Movie e MovieProgress. O campo **Ano de lançamento** fica no inline Movie dentro do cadastro de Activity, preservando a experiência de cadastrar uma atividade.

Não confundir ano de lançamento com ano da cerimônia do Oscar. Ambos aparecem no detalhe; ordenar a coleção pelo ano da cerimônia, depois lançamento, nome e ID.

### 3.2 Significado de assistir

- **Começar a assistir**: status watching, guarda started_at; não abre automaticamente timer ou serviço de streaming.
- **Marcar como assistido**: status watched e watched_at; pode ser usado diretamente para filmes vistos anteriormente.
- **Não assistir agora / Sortear outro**: libera a escolha; o filme volta a ser elegível. Não conta como assistido.
- **Desfazer assistido**: volta a unwatched; limpa watched_at, preserva evento histórico.
- Filmes watching aparecem em “Continuar assistindo” e não entram em novo sorteio até serem concluídos ou devolvidos a unwatched.
- Um filme somente sorteado permanece unwatched e reservado pela escolha pendente.

A exclusão permanente do sorteio depende de watched. Reservas e watching são exclusões temporárias explícitas. Cada filme elegível tem a mesma probabilidade; prioridade, premium e limites diários da categoria não interferem.

### 3.3 Escopo do MVP

Inclui cadastro manual no Django Admin, coleção Oscar, catálogo, roleta, escolha recuperável, progresso, histórico e desfazer conclusão. Não inclui reprodução de filmes, descoberta automática de streaming, scraping do Instagram, cadastro Flutter, avaliações, recomendações ou reinício em massa.

Não carregar automaticamente uma lista de vencedores nesta etapa. Uma importação futura deverá ser versionada, idempotente, com dry-run e fonte oficial verificada. Não fixar quantidade total ou lista “atual” sem conferência.

## 4. Modelo de dados proposto

| Modelo | Campos principais | Regras |
|---|---|---|
| MovieCollection | slug único, name, category FK PROTECT, active, timestamps | Inicial slug oscar-best-picture; categoria selecionada explicitamente |
| Movie | activity OneToOne PROTECT, release_year, runtime_minutes opcional, poster_url opcional, watch_url opcional, active, timestamps | Nome/descrição vêm de Activity; release_year obrigatório |
| MovieCollectionEntry | collection FK PROTECT, movie FK PROTECT, award_year, award_edition opcional, sort_order opcional | Único (collection, movie); filme deve pertencer à categoria da coleção |
| MovieProgress | scope_key, movie FK PROTECT, status, started_at, watched_at, version, timestamps | Único (scope_key, movie); versão inicial virtual 0, persistida ≥1 |
| MovieDrawState | scope_key, collection FK PROTECT, current_draw FK nullable, version | Único (scope_key, collection); linha usada para serializar mutações |
| MovieDraw | UUID, scope_key, collection/movie FK PROTECT, request_id UUID, payload_hash, status, eligible_count, candidate_snapshot JSON, animation_duration_ms, selected_at, resolved_at | Único (scope_key, request_id); status pending/accepted/dismissed/invalidated |
| MovieProgressEvent | scope_key, movie FK PROTECT, request_id UUID, previous_status, next_status, occurred_at, draw FK opcional | Histórico imutável, único (scope_key, request_id) |

MovieCollectionEntry separa dados da premiação dos dados do filme e permite outras coleções posteriormente. No MVP um filme usa a categoria Oscar; não implementar múltiplas categorias por Activity. Progresso é por filme, mesmo que futuras coleções compartilhem o título.

Validações:

- Anos inteiros entre 1888 e 2100; duração opcional entre 1 e 1440 minutos. award_edition positivo quando informado.
- watched exige watched_at; outros estados exigem watched_at nulo. watching exige started_at. unwatched limpa started_at no estado atual; eventos preservam transições.
- URL somente HTTP/HTTPS; watch_url é link editorial opcional. Não buscar URLs no servidor. Front abre somente após ação explícita.
- Atividade, filme e coleção ativos para sortear. Mudar categoria de filme vinculado deve validar a compatibilidade das coleções também no fluxo genérico de edição de Activity.
- Não impor unicidade de award_year: cadastros históricos exigem revisão editorial, não suposição de exatamente um registro por ano.
- Desativar para retirar do catálogo; não apagar histórico. IDs opacos/autorizados devem ser resolvidos no escopo correto.

Activity.duration continua sendo duração de bloco Pomodoro; Movie.runtime_minutes é duração do longa. Um campo não preenche o outro implicitamente.

## 5. Sorteio, persistência e concorrência

1. Cliente gera request_id antes do POST e o preserva em timeout/retry.
2. Serviço abre transaction.atomic, cria/obtém MovieDrawState com proteção por unicidade e bloqueia a linha com select_for_update. Todas as ações da coleção seguem essa ordem de bloqueio.
3. Se request_id existir, validar payload_hash e retornar o mesmo sorteio; payload diferente retorna 409 idempotency_conflict.
4. Se houver sorteio pending válido, retornar 409 pending_draw_exists com ID recuperável. Não realizar outro sorteio silencioso.
5. Consultar TODOS os candidatos elegíveis, sem usar apenas a página visível, excluindo watched e watching no scope_key atual.
6. Escolher uniformemente um ID no backend, usando fonte aleatória apropriada como secrets.choice; salvar resultado, snapshot ordenado de candidatos e duração inteira entre 5000 e 10000 ms.
7. Persistir a escolha antes de responder. A animação é apresentação; não demora a resposta HTTP nem determina o vencedor.
8. Cliente anima o snapshot e posiciona o ponteiro exatamente no índice retornado.
9. Aceitar, descartar e alterar progresso revalidam filme ativo, elegibilidade e versões sob transação. Progresso usa bloqueio por filme/escopo; criação concorrente precisa tratar a ausência da linha com unicidade e retry controlado.
10. Atualização de progresso em outra coleção/dispositivo pode tornar uma escolha inelegível. Invalidar a reserva ao revalidar e informar a tela; nunca aceitar um filme já watched como watching.

Sorteios são serializados por coleção/escopo. Diferentes coleções podem reservar o mesmo filme no futuro; progresso compartilhado e revalidação impedem conclusões contraditórias. Para o MVP há uma coleção.

GET de estado reavalia a escolha atual e sinaliza invalidade sem mutação oculta. Uma ação explícita descarta/invalida a reserva. Se candidato escolhido foi desativado, preservar registro histórico, impedir aceitação e permitir novo sorteio após liberação.

Fallbacks:

- Zero candidatos e todos assistidos: collection_completed.
- Zero candidatos com filmes watching: no_eligible_movies, sugerir continuar ou devolver filme ao sorteio.
- Catálogo vazio: empty_collection, sugerir cadastro.
- Um candidato: mostrar o único filme e permitir animação abreviada acessível; servidor mantém o mesmo contrato de resultado.
- Fechar tela/app: reserva permanece; ao voltar exibir resultado salvo sem exigir nova animação.
- Timeout: recuperar pelo request_id; não gerar novo UUID automaticamente.

## 6. Contrato HTTP proposto

Rotas relativas ao prefixo API já utilizado pelo app. HasAPIKey e build_scope_key(request) em todas; cliente nunca escolhe scope_key no payload.

| Método e rota | Uso |
|---|---|
| GET movie-collections/ | Coleções ativas e contadores por escopo |
| GET movie-collections/{id}/state/ | Escolha atual, version, contadores, condição do catálogo |
| GET movies/?collection_id=…&status=…&search=…&page=… | Catálogo paginado, dados e progresso; status unwatched/watching/watched |
| GET movies/{id}/ | Detalhe com progresso autorizado |
| POST movie-collections/{id}/draws/ | Sorteia com request_id e expected_state_version |
| GET movie-collections/{id}/draws/by-request/{request_id}/ | Recupera resultado após timeout |
| POST movie-collections/{id}/draws/{uuid}/accept/ | Começar a assistir: request_id, expected_state_version, expected_progress_version |
| POST movie-collections/{id}/draws/{uuid}/dismiss/ | Liberar escolha: request_id, expected_state_version |
| PATCH movies/{id}/progress/ | request_id, expected_version, status; draw_id opcional validado |
| GET movies/progress-history/?collection_id=…&page=… | Eventos pessoais paginados |

POST de sorteio novo retorna 201; replay idêntico retorna 200. Recuperação ausente retorna 404. Mutação concluída retorna 200 com progresso e estado atualizados. PATCH watched ligado à reserva resolve a reserva como accepted na mesma transação. watched manual também libera qualquer escolha pending desse filme na coleção MVP.

Exemplo de resposta de sorteio (valores ilustrativos, sem catálogo histórico):

```json
{
  "id": "uuid-do-sorteio",
  "request_id": "uuid-do-cliente",
  "status": "pending",
  "state_version": 4,
  "animation_duration_ms": 7000,
  "eligible_count": 2,
  "selected_index": 1,
  "candidates": [
    {"movie_id": 12, "name": "Filme A", "release_year": 1990},
    {"movie_id": 25, "name": "Filme B", "release_year": 2000}
  ],
  "movie": {
    "id": 25,
    "activity_id": 50,
    "name": "Filme B",
    "release_year": 2000,
    "award_year": 2001,
    "poster_url": null,
    "watch_url": null,
    "progress": {"status": "unwatched", "version": 0}
  }
}
```

Contadores: total_active, watched, watching, unwatched e eligible_count. total_active é o denominador atual; eventos de filmes desativados continuam no histórico. Um pending reduz eligible_count em uma unidade no estado corrente, mas não altera unwatched. Na resposta do sorteio eligible_count é o número de candidatos ANTES da escolha.

Erros JSON com code/detail: 400 invalid_input; 403 sem permissão conforme contrato existente; 404 registro inacessível/inexistente; 409 stale_state_version, stale_progress_version, pending_draw_exists, idempotency_conflict, movie_unavailable, collection_completed, no_eligible_movies, empty_collection. Erro de rede é estado do cliente, não novo sorteio.

## 7. Django Admin e comportamento das filas

- Movie inline no cadastro de Activity; exibir ano, duração do longa e pôster sem duplicar nome.
- Admin de MovieCollection com entries inline; selecionar categoria e filmes e informar ano da premiação.
- Filtros por coleção, categoria, lançamento, premiação e ativo; progresso/eventos consultáveis com cuidado ao scope, eventos somente leitura.
- Não esconder filmes marcando Activity.active=false: isso também impediria o catálogo.
- No MVP filmes vinculados a Movie são atividades exclusivas desse menu: excluir de composição/reconciliação de filas genéricas, inclusive grupo Todos. Auditar pontos de criação e recriação, não apenas a apresentação do próximo item.
- Ao converter Activity existente em Movie, retirar somente itens futuros não iniciados por reconciliação; preservar Schedule, History e execuções abertas. Não concluir ou cancelar sessões automaticamente.
- Desativar Movie não deve reinserir Activity silenciosamente nas filas. A condição de exclusão é existir vínculo Movie, independentemente de Movie.active.
- Não mudar limite diário de Entretenimento/Oscar para acomodar a roleta. Progresso cinematográfico não altera executions_today, last_executed, GoalCompletion ou metas.

Integração com cronômetro pode ser uma fase futura, com origem explícita e critério independente de “filme assistido”. Terminar um bloco nunca significa automaticamente terminar o longa.

## 8. Frontend Flutter

### Navegação e tela

Desktop: adicionar DesktopSection.movies e tela embedded seguindo RetroGames. Mobile: adicionar acesso Filmes preservando legibilidade da barra; se necessário usar menu “Mais” em vez de comprimir os destinos existentes.

Layout desktop: cabeçalho “Oscar — Melhor Filme”, progresso e botão de atualizar; roleta à esquerda, escolha/detalhe à direita e catálogo abaixo. Mobile: coluna rolável, roleta, resultado e abas/lista de catálogo e assistidos.

Wireframe funcional:

```text
Filmes / Oscar — Melhor Filme          [Atualizar]
Assistidos X de Y · Em andamento Z
[barra de progresso]

       [ponteiro]
       [roleta]          Escolha: Nome do filme
   [Girar roleta]        Lançamento · Premiação · Duração
                        [Começar a assistir]
                        [Marcar como assistido]
                        [Não assistir agora]

[Buscar] [Não assistidos | Em andamento | Assistidos]
Catálogo com título, ano e estado
```

### Animação

- CustomPainter e AnimationController; usar tema/tokens existentes, sem dependência nova obrigatória.
- Snapshot completo recebido do backend com índices estáveis. Em muitos segmentos usar números/cores e legenda pesquisável, destacar título escolhido em texto grande. Não apresentar uma amostra como se fosse o universo sorteado.
- Ângulos iguais por candidato; voltas completas adicionais e desaceleração até o centro do setor escolhido. Fórmula/teste deve considerar posição fixa do ponteiro e orientação do canvas.
- Bloquear duplo clique e mudanças de filtro durante a requisição/animação. A duração visual começa após receber o resultado.
- Acessibilidade: rótulos semânticos, resultado anunciado ao terminar, contraste, fonte escalável, teclado e opção “Pular animação”. Respeitar preferência de movimento reduzido; isso não muda o sorteio.
- Sons/confetes opcionais após MVP; desligados por padrão.

### Estado e rede

MoviesController com estados loading, ready, requestingDraw, spinning, result, mutating, completed e error. Estado de rede separado de progresso da animação. UI stale pode mostrar catálogo em cache, mas não sortear nem concluir offline.

Guardar localmente apenas request_id pendente por escopo/coleção para recuperação; servidor é a fonte do vencedor e do progresso. Em troca de credencial/escopo, limpar estado em memória/cache associado. Respostas antigas devem ser descartadas pelo token de requisição, como no padrão atual.

Distinguir falha de carregar catálogo, falha de sortear e falha de gravar assistido. Se gravação falhar, manter resultado e oferecer retry com o mesmo request_id. Em 409 atualizar estado e apresentar mensagem específica. Iniciar aceitação ou conclusão sempre usa versão recebida mais recente.

## 9. Arquivos e sequência de implementação

Esta task entrega planejamento; não aplica modelos, migrações ou telas.

1. **Backend / domínio**: models.py, nova migration, admin.py; constraints, cadastro e compatibilidade de categorias. Revisar Activity serializers/admin para edições genéricas.
2. **Backend / serviços**: services/movie_draw.py, movie_progress.py, movie_catalog.py e repositories/movies.py; seleção, locks, idempotência, contadores e reconciliação de reservas.
3. **Backend / contrato**: movie_serializers.py, movie_views.py e urls.py; validação/erros e fixtures sintéticas.
4. **Backend / filas**: services/activity_queue.py e activity_queue_reconciliation.py e demais seletores identificados na implementação; excluir vínculos Movie de filas preservando histórico.
5. **Frontend / dados**: lib/models/movies.dart, lib/services/movies_api.dart, adaptação de ApiService e lib/controllers/movies_controller.dart.
6. **Frontend / UI**: lib/screens/movies_screen.dart, lib/widgets/movie_roulette.dart, movie_result_card.dart e movie_catalog_list.dart; integrar shell e telas home.
7. **Validação conjunta**: contrato, acessibilidade, responsividade, retries, duas abas/dispositivos e catálogo grande.
8. **Carga editorial**: cadastrar coleção/categoria e pequeno conjunto de filmes para homologação. Só depois importar todos os vencedores com fonte revisada, sem hardcode de ID de grupo.

Entregar primeiro um fluxo vertical com dados sintéticos: cadastrar → sortear → recuperar → assistir → excluir dos próximos sorteios. Depois completar catálogo/histórico e ajustes visuais.

## 10. Critérios de aceite e testes necessários

- [ ] Activity comum continua sem exigir ano; Movie exige ano de lançamento.
- [ ] Filmes vinculados aparecem somente no menu próprio, sem contaminar filas gerais.
- [ ] Roleta inclui todos os elegíveis, inclusive fora da primeira página; não usa prioridade/cotas.
- [ ] watched nunca é sorteado no mesmo scope; progresso de outro scope não interfere.
- [ ] Clique em começar registra watching; só confirmação explícita registra watched.
- [ ] Animação dura 5–10 segundos no modo normal e termina no vencedor persistido.
- [ ] Timeout/retry com mesmo request_id produz o mesmo resultado e um único registro.
- [ ] Dois POSTs simultâneos na mesma coleção não deixam duas reservas pending.
- [ ] Duas mutações de progresso incompatíveis retornam conflito; criação simultânea também é coberta.
- [ ] Fechar/reabrir recupera escolha; dispensar devolve filme; desfazer assistido torna filme elegível.
- [ ] Inativação ou mudança de progresso durante animação não permite aceitação inválida.
- [ ] Vazio, um candidato, todos assistidos e apenas filmes watching têm mensagens distintas.
- [ ] Desfazer preserva evento histórico; atualização repetida idempotente não duplica evento.
- [ ] URL ausente/pôster quebrado não bloqueia o fluxo; abrir link não altera progresso.
- [ ] Longos títulos, catálogo grande, mobile pequeno e movimento reduzido funcionam.

Backend: testes unitários do conjunto elegível/índice; integração de API, isolamento e idempotência; regressão das filas. Não usar teste estatístico probabilístico como único critério de igualdade: validar população sem duplicatas e seleção uniforme por índice com fonte aleatória controlada.

Banco de testes isolado conforme personal-python-api, sem fallback para DATABASE_URL real. Testes de locks e concorrência precisam de PostgreSQL explicitamente descartável; SQLite não comprova select_for_update. Front: testes do parsing, máquina de estados/retry, widget de resultado e matemática de ângulos com relógio controlado; flutter analyze e testes relevantes.

## 11. Riscos, evolução e decisões propostas

- O scope atual é identidade lógica existente, não necessariamente uma pessoa. Dispositivos que compartilham identidade podem compartilhar progresso; documentar esse comportamento antes do uso multiusuário.
- Exclusão de filmes das filas é mudança transversal: auditar todos os seletores e preservar itens/sessões já iniciados.
- Ano do prêmio e do lançamento são distintos. Não inferir automaticamente um a partir do outro.
- URLs externas e disponibilidade de streaming podem mudar. watch_url é opcional e manual; não prometer reprodução dentro do app.
- Pôsteres precisam de origem e uso adequados; placeholder obrigatório.
- A coleção pode crescer: progresso acompanha o catálogo ativo e não reinicia ao cadastrar novos filmes.
- Reassistir em ciclos, bloquear filmes permanentemente, notas pessoais, avaliação, importação oficial e integração de timer são extensões futuras.

Decisões padrão deste plano: nome do menu Filmes; coleção inicial Oscar; cadastro manual no admin; Movie estende Activity; progresso separado por scope; escolha persistente; começar não conclui; sem timer obrigatório. Podem ser ajustadas antes da implementação sem migrar dados, pois esta spec permanece PROPOSED.
