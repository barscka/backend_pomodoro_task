# Tier list pessoal dos filmes

## Comportamento

A classificação pertence à mesma identidade lógica do progresso de filmes (`scope_key`), separada por coleção. Quem compartilha a mesma identidade/API key compartilha a tier list, conforme o contrato atual do app. Somente filmes marcados como assistidos podem receber S, A, B, C ou D. Assistir não atribui um tier automaticamente. É possível reavaliar ou remover a classificação sem alterar o progresso, a roleta ou o histórico de assistidos.

Limites máximos: S = 10; A = 20; B = 20; C = 20; D = 30. São 100 vagas para 97 vencedores, com três vagas de folga quando todos estiverem classificados. Não são cotas mínimas, médias numéricas ou preenchimento obrigatório. Os limites são fixos nesta versão.

O frontend exibe “Minha tier list”, contadores usados/limite e grupos expansíveis com todos os assistidos, inclusive os sem classificação. Cada filme assistido tem um combobox. Ao escolher um tier cheio, o app pede um filme desse tier para deslocar e seu destino. O destino pode ser outro tier ou “Sem classificação”. Se o filme que entra já tem um tier, esse tier é sugerido para permitir a troca direta. Cancelar mantém a avaliação atual. Não há deslocamentos automáticos ou escolhas feitas pelo app.

Desfazer assistido ou voltar a “em andamento” remove a avaliação desse filme em todas as coleções da identidade, na mesma transação do progresso. Marcar assistido novamente não recupera a nota antiga: o usuário reavalia. Inativar um filme não libera silenciosamente sua vaga; ele continua na tier list enquanto constar na coleção e permanecer assistido.

## Contrato HTTP

`GET /api/movie-collections/{id}/tiers/` retorna:

```json
{
  "collection_id": 1,
  "version": 4,
  "limits": {"S": 10, "A": 20, "B": 20, "C": 20, "D": 30},
  "counts": {"S": 10, "A": 3, "B": 0, "C": 0, "D": 0},
  "unrated_count": 1,
  "watched_count": 14,
  "items": [
    {"movie_id": 9, "name": "Asas", "release_year": 1927, "tier": "A", "active": true}
  ]
}
```

O exemplo abrevia `items`; a resposta real inclui todos os filmes assistidos da coleção. Não há paginação na tier list para permitir rebalanceamento entre todas as avaliações. Catálogo e histórico mantêm a paginação existente.

`PATCH` na mesma URL aceita a avaliação individual ou um lote (até 100 filmes distintos):

```json
{
  "request_id": "11111111-1111-4111-8111-111111111111",
  "expected_version": 4,
  "moves": [
    {"movie_id": 9, "tier": "S"},
    {"movie_id": 10, "tier": "A"}
  ]
}
```

`tier: null` remove a avaliação. O servidor valida a distribuição final do lote antes de gravar qualquer movimento, permitindo trocar dois tiers cheios. Retorna a tier list completa atualizada. Payload com tier inválido, IDs repetidos, lista vazia ou campo desconhecido retorna 400. Conflitos retornam 409 com `code`: `tier_full`, `movie_not_watched`, `movie_outside_collection`, `stale_tier_version` ou `idempotency_conflict`. Registro inexistente retorna 404; identidade sem API key válida retorna 403.

O servidor usa o mutex de escopo existente de Filmes, compartilhado com alterações do progresso, e uma versão própria de tier list. Isso serializa escritas concorrentes sem alterar as versões de sorteio. `request_id` reutilizado com payload idêntico devolve a resposta original; payload diferente com o mesmo UUID é rejeitado. O Flutter persiste a operação antes de enviar e repete o mesmo UUID/lote em caso de timeout. Após resposta, recarrega o estado canônico. Após conflito, atualiza e pede nova decisão; não repete automaticamente uma troca baseada em dados antigos.

## Arquivos e implantação

Backend: modelos `MovieTierState`/`MovieTierRating`, migração `0025_movietierrating_movietierstate`, service `movie_tiers`, serializers e action da coleção. O service de progresso limpa ratings ao desfazer assistido. Frontend: modelo `movie_tiers`, cliente HTTP, controller de Filmes e widget `movie_tier_list` integrado à tela existente. Nenhuma dependência nova.

Publicar primeiro o backend e aplicar `python manage.py migrate --noinput`; depois publicar o frontend. A migração só cria tabelas, sem alterar filmes, progresso ou CSV. O frontend mostra um erro separado se a tier list não puder ser carregada e mantém o restante do módulo Filmes funcionando. Não houve alteração de dados ou implantação em produção nesta tarefa.

Validações: testes backend de todas as capacidades, troca atômica, reavaliação, remoção, idempotência, versões, isolamento de identidade, campos inválidos, autorização e desfazer assistido; testes Flutter de rota/autenticação/lote, timeout/replay, conflito, cancelamento, troca e layout estreito. Padrões aplicados: personal-python-api, personal-flutter e personal-dev-workflow. Testes de banco usam SQLite isolado; a concorrência real em PostgreSQL não foi executada nesta tarefa.

Resultado da entrega: 307 testes backend aprovados (20 skips de testes PostgreSQL); 56 testes Flutter do módulo Filmes aprovados; `flutter analyze` sem problemas; `makemigrations --check --dry-run` sem mudanças pendentes; `git diff --check` aprovado em ambos os repositórios. A migração nova foi aplicada no banco isolado criado e destruído pelo ciclo dos testes.
