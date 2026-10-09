# Importação dos vencedores do Oscar pelo Django Admin

## Uso

1. Atualize as dependências com `poetry install --only main --no-root` (ou reconstrua a imagem Docker, que já instala pelo lockfile).
2. Aplique a migração `0024_movieimportjob` e inicie o backend com o worker conforme a seção abaixo. Em implantação com arquivos estáticos, execute `collectstatic` pelo fluxo habitual.
3. No Admin, abra **Pomodoro → Movies → Importar** (`/admin/pomodoro/movie/import/`).
4. Selecione o formato CSV e envie `apps/pomodoro/data/oscar_best_picture_ate_2025.csv`.
5. O upload redireciona imediatamente para o acompanhamento. Aguarde a prévia, revise todas as linhas e confirme. A gravação também ocorre em segundo plano; acompanhe até “Importação concluída”.
6. Verifique a coleção **Oscar — Melhor Filme** no Admin e no menu Filmes do app.

Se o Admin rejeitar `description` como coluna desconhecida, o servidor está executando o importador anterior. Implante a versão que inclui essa coluna e reinicie/recrie o serviço Django antes de usar o CSV atualizado. Remover a coluna permite usar o formato antigo, mas não importa as descrições. A validação de cabeçalhos agora interrompe o arquivo antes de processar linhas, evitando erros secundários de `_seen`.

## Processamento assíncrono e implantação

A fila `MovieImportJob` persiste CSV, solicitante, etapa, datas e relatório no banco. Um processo separado executa `OscarMovieResource` do django-import-export para prévia (`dry_run`) e gravação. Nenhuma importação é executada dentro da requisição HTTP. Não há thread dentro do Gunicorn, Redis, Celery ou nova dependência Python. Limites: CSV UTF-8 de 2 MB e até 1.000 filmes.

Estados: aguardando prévia → preparando prévia → prévia pronta → confirmação por POST → aguardando importação → importando → concluída ou falha. O Admin atualiza o acompanhamento a cada 5 segundos enquanto houver processamento. Cada usuário vê seu histórico; superusuários podem abrir trabalhos de outros usuários. As permissões completas de importação são exigidas nas páginas e reavaliadas pelo worker antes de cada etapa. Duplo clique na confirmação não duplica o trabalho.

Depois de atualizar o código/imagem, no servidor:

```bash
docker compose build backend movie-import-worker
docker compose run --rm backend python manage.py migrate --noinput
docker compose up -d --build backend movie-import-worker
docker compose logs --tail=100 movie-import-worker
```

O worker usa a mesma imagem, configuração e banco do backend e inicia após o healthcheck da aplicação. `RUN_COLLECTSTATIC=false` evita coleta concorrente de estáticos no worker. Mantenha uma instância do worker. Fora do Docker: `python manage.py process_movie_imports`; para execução pontual: acrescente `--once`.

Prévia não persiste alterações do catálogo. Importação e resultado final são confirmados na mesma transação: erro ou interrupção reverte a gravação inteira. Um worker morto pode deixar a etapa como “importando” ou “preparando prévia”; com TODOS os workers parados, marque os trabalhos interrompidos como falha e reinicie:

```bash
docker compose stop movie-import-worker
docker compose run --rm --no-deps movie-import-worker python manage.py process_movie_imports --recover-interrupted
docker compose up -d movie-import-worker
```

Depois, reenvie o CSV e revise a nova prévia. Trabalhos ainda na fila são mantidos. Não há repetição automática de uma gravação interrompida. O CSV e o relatório permanecem no banco para consulta; não há limpeza automática nesta versão. Se continuar “aguardando”, verifique o serviço worker e a migração. O endpoint antigo `/process_import/` foi desativado para não executar importações síncronas.

O arquivo é UTF-8, separado por vírgulas, com cabeçalho e 97 registros. UTF-8 com BOM também é aceito. Os nomes usam os títulos brasileiros; títulos sem tradução comercial mantêm o nome conhecido no Brasil. A coluna opcional `description` registra título original, disponibilidade no Brasil, data e URL da fonte. Duração, pôster e link direto de assistir ficam vazios para preenchimento editorial posterior.

As descrições distinguem assinatura, gratuito (eventualmente com anúncios) e aluguel/compra. Canais pagos dentro de Amazon ou Apple aparecem com seu nome completo: não significa inclusão na assinatura básica. Em sete filmes, a fonte não lista nenhuma oferta brasileira; isso não prova indisponibilidade em todos os serviços existentes. Foram consultadas páginas regionais do JustWatch em 09/10/2026, que podem conter dados em cache; quando presente, a data da última checagem informada pela página também consta na descrição. Os catálogos mudam e não há atualização automática.

O usuário do Admin precisa das permissões de adicionar e alterar Movie, Activity, Group, Category, MovieCollection e MovieCollectionEntry; superusuários já possuem essas permissões. Não é necessário permitir apagar registros ou alterar progresso pessoal.

## Cadastro e atualização

- `group_name=Entretenimento`, `category_name=Oscar`, `collection_slug=oscar-best-picture` e `collection_name=Oscar — Melhor Filme` vêm no CSV. O importador cria os registros ausentes e reutiliza os existentes, sem IDs fixos.
- Se a categoria Oscar já pertencer a outro grupo, ou a coleção já tiver nome/categoria diferentes, a importação é rejeitada sem mover dados. Ajuste os rótulos no CSV para corresponder ao cadastro desejado, mantendo o slug obrigatório.
- Cada linha cria/reutiliza Activity, Movie e MovieCollectionEntry. A identidade estável é Activity.external_source=`oscar-best-picture` e external_id igual à edição do prêmio. Não alterar award_edition para renomear um filme.
- Reimportar atualiza o título, descrição preenchida, lançamento e dados da premiação, sem duplicar registros. A descrição é gravada em `Activity.description`. O CSV anterior com títulos originais pode ser reimportado com os títulos brasileiros, mantendo identidade e progresso pela edição. Um Movie cadastrado manualmente com mesmo título, ano e categoria pode ser vinculado se não tiver outra identidade externa. Correspondências ambíguas são rejeitadas.
- Uma Activity comum com mesmo nome e categoria não é convertida automaticamente: vincule Movie manualmente e reimporte. Isso evita converter uma tarefa normal por coincidência de título.
- Campos opcionais vazios preservam duração, pôster, URL e descrição existentes; para limpar um campo use o Admin. CSVs antigos sem a coluna `description` continuam aceitos. Importação não reativa registros, altera duração de bloco, premium ou progresso de assistidos.
- O save normal de Movie reconcilia filas conforme a SPEC-018. Itens futuros podem ser retirados; sessões abertas e histórico são preservados. A prévia assíncrona simula isso em transação e faz rollback.
- Arquivo inteiro é transacional: erro de validação ou persistência em qualquer linha reverte todos os registros do arquivo. A confirmação reexecuta as validações, inclusive se o banco mudou depois da prévia.
- Edições repetidas no arquivo, colunas desconhecidas/duplicadas, campos obrigatórios ausentes, anos inválidos e URLs fora de HTTP/HTTPS são rejeitados.
- Esta tela importa CSV cinematográfico; não habilita importação de progresso, sorteios, usuários ou exclusão de filmes.

## Recorte e fontes editoriais

“Até 2025” significa cerimônias até a 97ª edição, realizada em 2025: o último filme é Anora (2024). Filmes lançados em 2025 e premiados em 2026 estão fora deste CSV.

`release_year` é o ano do lançamento/estreia original, inclusive festival; `award_year` é o ano civil da cerimônia e `award_edition` é o número da edição. A Academia também classifica sua base por ano de elegibilidade, que não deve ser copiado como ano da cerimônia. Duas cerimônias ocorreram em 1930; a 6ª edição ocorreu em 1934. Casablanca é de 1942, premiado em 1944; Crash estreou em 2004 e foi premiado em 2006; The Hurt Locker estreou em 2008 e foi premiado em 2010.

Fontes consultadas em 2026-10-09:

- [Base oficial da Academia, vencedores da categoria histórica de Melhor Filme, edições 1–93](https://awardsdatabase.oscars.org/search/getresults?query=%7B%22Sort%22%3A%223-Award%20Category-Chron%22%2C%22AwardCategory%22%3A%5B%2219%22%5D%2C%22AwardShowNumberFrom%22%3A1%2C%22AwardShowNumberTo%22%3A93%2C%22Search%22%3A30%2C%22IsWinnersOnly%22%3Atrue%7D).
- [Cerimônia de 1934](https://www.oscars.org/oscars/ceremonies/1934).
- Cerimônias oficiais de [2022](https://www.oscars.org/oscars/ceremonies/2022), [2023](https://www.oscars.org/oscars/ceremonies/2023), [2024](https://www.oscars.org/oscars/ceremonies/2024) e [2025](https://www.oscars.org/oscars/ceremonies/2025).
- [Wings, Paramount: lançamento em 1927](https://www.paramountpictures.com/movies/wings).
- [Casablanca, AFI: 1942](https://catalog.afi.com/Catalog/moviedetails/27175).
- [Crash, Festival de Deauville: 2004](https://www.festival-deauville.com/en/movies/crash/).
- [The Hurt Locker, arquivo da Bienal de Veneza: 2008](https://asac.labiennale.org/collezioni/fototeca/127600).
- Disponibilidade brasileira: páginas regionais do JustWatch, com um link por filme na descrição, por exemplo [Asas](https://www.justwatch.com/br/filme/asas), [Titanic (1997)](https://www.justwatch.com/br/filme/titanic-1997) e [Anora](https://www.justwatch.com/br/filme/anora). Foram conferidos ano e versão para evitar remakes e títulos homônimos; ofertas estrangeiras foram ignoradas quando a página indicava ausência de opções no Brasil.

O CSV segue a linhagem oficial da categoria de Melhor Filme. Não inclui Sunrise, que venceu a categoria distinta Unique and Artistic Picture na primeira cerimônia.

## Dependência e validação

Plano aplicado: adicionar somente django-import-export 4.4.1 e suas dependências transitivas (tablib e diff-match-patch), habilitar import_export em INSTALLED_APPS e integrar ImportMixin no MovieAdmin. O lockfile preserva as versões das dependências preexistentes; sem upgrade de Django/Python e sem alteração do Dockerfile. Referência: [documentação oficial do fluxo de importação](https://django-import-export.readthedocs.io/en/latest/import_workflow.html).

Testes em banco SQLite isolado, nunca banco operacional:

```bash
APP_ENV=test TEST_DATABASE_URL=sqlite:///tests/.tmp/movie_import.sqlite .venv/bin/python manage.py check --settings=config.settings.test
APP_ENV=test TEST_DATABASE_URL=sqlite:///tests/.tmp/movie_import.sqlite .venv/bin/python manage.py test apps.pomodoro.test_movie_import apps.pomodoro.test_spec_018 --settings=config.settings.test --noinput
APP_ENV=test TEST_DATABASE_URL=sqlite:///tests/.tmp/movie_import.sqlite .venv/bin/python manage.py makemigrations --check --dry-run --settings=config.settings.test
git diff --check
```

Os testes cobrem prévia sem persistência, carga de 97 registros, reimportação preservando progresso/metadata/inatividade, tradução e descrição sem troca de identidade, descrição vazia e CSV legado preservando conteúdo existente, rollback de arquivo inválido, categoria incompatível, vínculo manual, proteção de Activity comum, cabeçalhos/anos, duas edições no mesmo ano e upload/confirmação/permissões no Admin.

Nenhuma carga no banco real foi executada nesta tarefa. Antes de importar, revise a prévia e eventuais rótulos já existentes. Importações simultâneas podem gerar conflito de unicidade e rollback; execute uma confirmação por vez.

Validações anteriores do importador: doctor aprovado; suíte completa em SQLite; system check sem problemas; poetry check --lock aprovado com avisos preexistentes; CSV com edições 1–97 únicas. A extensão assíncrona tem testes de fila durável, CSV completo, confirmação idempotente, rollback de arquivo, permissões revogadas, CSRF, upload inválido, recuperação e bloqueio do endpoint síncrono. PostgreSQL e implantação real não foram usados nesta tarefa: não houve mudança nas regras de locks do sorteio.

Validação da extensão assíncrona: suíte de 303 testes aprovada em SQLite (20 skips de testes que exigem PostgreSQL); configuração Compose e sintaxe do entrypoint válidas; migração 0024 exercitada no banco isolado; `makemigrations --check --dry-run` e `git diff --check` aprovados. Padrões aplicados: personal-python-api (Django, banco e testes) e personal-dev-workflow.
