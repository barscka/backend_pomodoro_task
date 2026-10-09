---
spec_id: SPEC-018
titulo: Catálogo de filmes e roleta do Oscar
status: BACKEND_IMPLEMENTED
fase: BACKEND_VALIDADO_FRONTEND_PENDENTE
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
- Migração aditiva entregue: 0023_moviescopelock_movie_moviecollection_and_more, dependente de 0022. Não há carga de vencedores nem alteração de dados existentes.

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

## 4. Modelo de dados entregue

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

## 6. Contrato HTTP entregue

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

Backend implementado nesta task. As etapas de frontend e carga editorial abaixo permanecem pendentes. Nenhum arquivo de frontend foi alterado.

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

- [x] Activity comum continua sem exigir ano; Movie exige ano de lançamento.
- [x] Filmes excluídos das filas gerais e da listagem comum de activities; menu próprio no frontend pendente.
- [x] Roleta inclui todos os elegíveis, inclusive fora da primeira página; não usa prioridade/cotas.
- [x] watched nunca é sorteado no mesmo scope; progresso de outro scope não interfere.
- [x] Backend de começar registra watching; só confirmação explícita registra watched (ação visual do frontend pendente).
- [ ] Animação do frontend dura 5–10 segundos e termina no vencedor; backend já persiste duração nesse intervalo e índice do vencedor.
- [x] Timeout/retry com mesmo request_id produz o mesmo resultado e um único registro.
- [x] Dois POSTs simultâneos na mesma coleção não deixam duas reservas pending.
- [x] Duas mutações de progresso incompatíveis retornam conflito; criação simultânea também é coberta.
- [x] API recupera escolha após timeout/reabertura, dispensa e desfaz conclusão; persistência local do request_id no frontend pendente.
- [x] Backend impede aceitação de escolha inativa ou com progresso incompatível; animação no frontend pendente.
- [x] Vazio, um candidato, todos assistidos e apenas filmes watching têm mensagens distintas.
- [x] Desfazer preserva evento histórico; atualização repetida idempotente não duplica evento.
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

Decisões padrão deste plano: nome do menu Filmes; coleção inicial Oscar; cadastro manual no admin; Movie estende Activity; progresso separado por scope; escolha persistente; começar não conclui; sem timer obrigatório. Backend implementado com as decisões e contratos finais da seção 12. Mudanças posteriores de contrato ou domínio devem considerar os dados persistidos.


## 12. Entrega backend e decisões técnicas finais

### 12.1 Persistência e concorrência

- Os sete modelos de domínio estão em `apps/pomodoro/movie_models.py`, reexportados por `models.py` no app existente. A migração 0023 é exclusivamente aditiva, com PROTECT, unicidades, checks de anos/duração/status/datas/versão/índice e índices por coleção, escopo, status e tempo.
- Acrescentados dois modelos internos: `MovieMutation` guarda hash e resposta JSON imutável para replay também de accept/dismiss; `MovieScopeLock` fornece uma linha única por identidade lógica. Não é um usuário novo nem muda a identidade existente.
- Ordem de locks: mutex do escopo → estados de todas as coleções em ordem de PK → Movie/Activity selecionados → progresso existente. `get_or_create` trata criação simultânea por unicidade e savepoint; a linha de mutex protege também progresso ainda ausente e UUID usado em operações/coleções diferentes. Para o MVP, comandos do mesmo escopo são serializados entre coleções; escopos diferentes continuam independentes. O custo cresce com o número de coleções, uma limitação deliberada deste catálogo pequeno.
- Todas as mutações são atômicas. Falha ao registrar evento reverte progresso, reserva e idempotência. O vencedor, snapshot completo, índice e duração são persistidos antes da resposta; não há espera pela animação.
- Hash SHA-256 inclui operação, alvo e payload validado/canonizado. Campos desconhecidos são rejeitados, inclusive `scope_key`. UUID é único entre todas as mutações de filmes no escopo, não apenas dentro de um endpoint.
- Replay é verificado antes das versões: payload idêntico retorna a resposta original, sem alterar versões nem duplicar eventos. Outro payload/operação/alvo com o mesmo UUID retorna `409 idempotency_conflict`. Erros não consomem UUID. Após alteração do payload ou atualização de versões, o cliente deve criar novo request_id; após timeout deve conservar o anterior.
- Cada comando de progresso novo aceito verifica a versão, incrementa-a e registra evento, inclusive se repetir o status com novo UUID. Virtualmente, ausência de progresso significa unwatched/version=0; primeira escrita persiste version=1.
- A versão de cada coleção afetada muda uma vez por comando; resolução e progresso na mesma transação não contam duas mudanças. Metadados editoriais não incrementam versão de estado, mas elegibilidade é sempre revalidada.
- Watching/watched resolvem como accepted as reservas do mesmo filme em todas as coleções daquele escopo. Desfazer volta a unwatched e limpa as duas datas, preservando eventos e sorteios. Começar não cria timer, History, GoalCompletion, metas nem contadores.
- GET state não escreve. Reserva inelegível aparece com `current_draw_valid=false`. Accept retorna movie_unavailable; o cliente usa dismiss explícito para invalidar/liberar e depois pode sortear novamente. POST draws nunca substitui reserva automaticamente, mesmo inválida. Estado, recuperação, replay e dismiss continuam disponíveis para uma coleção desativada, para preservar a escolha após timeout e liberar a reserva; sortear de novo ou aceitar exige coleção ativa.
- Nenhum grupo/categoria/coleção foi semeado por migração: cadastrar categoria e coleção manualmente, usando slug `oscar-best-picture` para a coleção inicial. Não foi importada lista histórica, nem acessado serviço externo.

### 12.2 Contratos finais para o frontend

Todos os caminhos da seção 6 foram implementados sob `/api/`, com HasAPIKey e a mesma `build_scope_key` usada nas filas. A identidade é lógica; compartilhar a credencial/identidade existente pode compartilhar progresso. Não enviar scope_key em JSON.

- Listas de coleções, catálogo e eventos: `{count, next, previous, results}`, page inicial 1, page_size padrão 30 e máximo 100. Não há limite de página aplicado à população de sorteio.
- GET collections: cada resultado contém `id, slug, name, category_id, collection_id, version, counts, condition, current_draw, current_draw_valid`.
- GET state: `{collection_id, version, counts, condition, current_draw, current_draw_valid}`. `counts` contém total_active/watched/watching/unwatched/eligible_count. condition: ready/empty_collection/collection_completed/no_eligible_movies. Uma reserva válida mantém condition=ready, mesmo que eligible_count=0; mostrar a escolha pendente.
- GET movies aceita collection_id, status, search, page e page_size. Busca por título, sem diferenciar maiúsculas. Ordem: premiação, lançamento, nome, ID; sort_order permanece metadado editorial sem sobrepor essa ordem no MVP. Sem filtro de coleção, award_year é null. Detalhe acrescenta `collections: [{collection_id, award_year, award_edition}]`, pois premiação pertence à entrada, não a Movie.
- Movie contém `id, activity_id, name, description, release_year, award_year, runtime_minutes, poster_url, watch_url, active, progress`. Progresso: `{status, version, started_at, watched_at}`; datas ISO-8601 ou null. Detalhe de filme desativado permanece consultável por API key, mas mutação de progresso exige Movie e Activity ativos.
- POST draws exige `{request_id: UUID, expected_state_version: inteiro >=0}`. Retorna os campos do exemplo da seção 6, além de selected_at/resolved_at e `state` completo. Novo sorteio: 201; replay: 200. `eligible_count` no resultado é a população anterior à reserva; `state.counts.eligible_count` desconta a reserva atual.
- GET by-request retorna o mesmo vencedor/snapshot/índice/duração persistidos, com status/progresso/estado atuais e state_version atual. Replay de POST retorna a resposta original: consultar GET state para obter versões atuais antes da próxima ação.
- Accept exige `{request_id, expected_state_version, expected_progress_version}`; dismiss exige `{request_id, expected_state_version}`. UUID do sorteio fica na rota. PATCH progress exige `{request_id, expected_version, status}`, com draw_id opcional; draw_id deve ser uma reserva pending válida do mesmo filme e escopo.
- Resposta de mutação: `{movie, progress, states}`. `states` lista as coleções vinculadas ao filme. Accept/dismiss também devolvem `state` da coleção da rota. Progresso e state/version nessa resposta são os valores pós-comando; não inferir incrementos no cliente.
- Evento paginado: `{id, movie_id, name, request_id, previous_status, next_status, occurred_at, draw_id}`. Não expõe scope_key nem possui endpoint de edição/exclusão.
- Erros de domínio: `{code, detail}` com HTTP 409 e códigos da seção 6. pending_draw_exists também inclui `draw_id` e `valid`; movie_unavailable na aceitação pode incluir draw_id. Validação usa HTTP 400/invalid_input e detail como mapa/lista de mensagens por campo. Registro ausente/inacessível: HTTP 404/not_found. Ausência/invalidade da API key mantém HTTP 403 e o formato já usado por HasAPIKey.
- Pôster/watch_url são opcionais e somente HTTP/HTTPS no cadastro validado; servidor não busca os links. runtime_minutes nunca altera Activity.duration.

### 12.3 Auditoria das filas e Admin

- Exclusão usa existência de Movie, inclusive inativo: eligible_activities, diagnóstico de fila vazia, activity_belongs_to_queue, elegibilidade individual, criação normal, revisão de pulados, recriação com pulados e promoção premium/reconciliação.
- Movie.save reconcilia filas ativas após cadastro/edição: expira pending/presented futuros também em Todos e revisão de pulados. Não insere atividades novas em revisão fechada. Expiração protege itens ligados a Schedule preparing/running; started/completed, sessões e histórico permanecem preservados.
- A listagem genérica de activities omite filmes; detalhe/edição e endpoints de histórico/sessão continuam acessíveis. Início por item de fila revalida activity_belongs_to_queue; consultas de sessão aberta continuam preservadas.
- Activity.can_execute devolve false para vínculo Movie. Mudanças genéricas de categoria são validadas tanto no serializer de Activity quanto no clean do model/Admin; MovieCollection e suas entradas também validam compatibilidade.
- Admin: Movie inline em Activity sem exclusão do vínculo; coleção com entradas inline; edição e filtros de metadados. Movie não pode ser apagado pelo admin para reinserir silenciosamente Activity. Progresso, sorteios, estado, mutex, ledger e eventos são consultáveis somente para leitura; eventos não permitem inclusão, alteração ou exclusão.

### 12.4 Comandos seguros e validações

Executar da raiz do backend, com o virtualenv existente. Não usar settings.local/production, DATABASE_URL de ambiente nem comandos de migration em banco compartilhado.

```bash
python3 /home/barscka/workspace/skills/skills_pessoais/tools/standards/doctor.py --project .
APP_ENV=test TEST_DATABASE_URL=sqlite:///tests/.tmp/spec018.sqlite .venv/bin/python manage.py check --settings=config.settings.test
APP_ENV=test TEST_DATABASE_URL=sqlite:///tests/.tmp/spec018.sqlite .venv/bin/python manage.py makemigrations --check --dry-run --settings=config.settings.test
APP_ENV=test TEST_DATABASE_URL=sqlite:///tests/.tmp/spec018.sqlite .venv/bin/python manage.py test --settings=config.settings.test --noinput
scripts/test_spec018_postgres.sh
git diff --check
```

O script PostgreSQL exige Docker e imagem postgres:16 já presente (`--pull=never`), cria container novo com banco spec018_test/credenciais sintéticas, dados somente em tmpfs, porta efêmera vinculada a 127.0.0.1, executa migrations/testes nesse banco e remove o container no trap. Não reutiliza volume, banco ou credencial existentes. O `--keepdb` vale somente durante a vida do container descartável. SQLite valida contratos/regras e não comprova locks; os testes de locks são explicitamente pulados ali.

Validações efetivamente executadas nesta entrega serão registradas no handoff da SPEC-018. Incluem catálogo sintético, todas as rotas, isolamento por API key, fonte aleatória controlada/população completa/índices e extremos de duração, versões, replay, recuperação, reserva inválida, rollback, progresso compartilhado entre coleções, metadados/constraints/Admin, criação/recriação/revisão/premium de filas e preservação de sessões/histórico. PostgreSQL cobre criação concorrente de mutex/estado/progresso, UUID simultâneo, disputa por reserva, accept versus conclusão e conflito do mesmo UUID entre coleções.

### 12.5 Pendências explícitas

- Frontend completo (seção 8), testes visuais/acessibilidade, armazenamento local do request_id e validação conjunta: não realizados nesta task exclusivamente backend.
- Cadastro editorial da coleção e filmes reais: não realizado. Todo dado de teste é sintético.
- Aplicação da migração em ambiente operacional: não realizada. Revisar janela e backup antes de implantação conforme fluxo do projeto.
- GETs podem observar atualização concorrente entre consultas; versões e revalidação nas mutações são a proteção contra escrita stale. Em conflito, recarregar estado/progresso.
- Ledger e snapshots não têm retenção/limpeza automática. Considerar crescimento do catálogo/histórico e custo de serialização por escopo antes de expandir para muitas coleções.
