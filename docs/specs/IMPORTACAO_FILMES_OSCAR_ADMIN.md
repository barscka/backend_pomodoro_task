# Importação dos vencedores do Oscar pelo Django Admin

## Uso

1. Atualize as dependências com `poetry install --only main --no-root` (ou reconstrua a imagem Docker, que já instala pelo lockfile).
2. Em implantação com arquivos estáticos, execute `collectstatic` pelo fluxo habitual e reinicie a aplicação. Não há nova migração nesta alteração.
3. No Admin, abra **Pomodoro → Movies → Importar** (`/admin/pomodoro/movie/import/`).
4. Selecione o formato CSV e envie `apps/pomodoro/data/oscar_best_picture_ate_2025.csv`.
5. Revise a prévia de todas as linhas. Se não houver erros, confirme a importação.
6. Verifique a coleção **Oscar — Melhor Filme** no Admin e no menu Filmes do app.

O arquivo é UTF-8, separado por vírgulas, com cabeçalho e 97 registros. UTF-8 com BOM também é aceito. São títulos originais; não há tradução automática. Duração, pôster e link de assistir ficam vazios para preenchimento editorial posterior.

O usuário do Admin precisa das permissões de adicionar e alterar Movie, Activity, Group, Category, MovieCollection e MovieCollectionEntry; superusuários já possuem essas permissões. Não é necessário permitir apagar registros ou alterar progresso pessoal.

## Cadastro e atualização

- `group_name=Entretenimento`, `category_name=Oscar`, `collection_slug=oscar-best-picture` e `collection_name=Oscar — Melhor Filme` vêm no CSV. O importador cria os registros ausentes e reutiliza os existentes, sem IDs fixos.
- Se a categoria Oscar já pertencer a outro grupo, ou a coleção já tiver nome/categoria diferentes, a importação é rejeitada sem mover dados. Ajuste os rótulos no CSV para corresponder ao cadastro desejado, mantendo o slug obrigatório.
- Cada linha cria/reutiliza Activity, Movie e MovieCollectionEntry. A identidade estável é Activity.external_source=`oscar-best-picture` e external_id igual à edição do prêmio. Não alterar award_edition para renomear um filme.
- Reimportar atualiza o título, lançamento e dados da premiação, sem duplicar registros. Um Movie cadastrado manualmente com mesmo título, ano e categoria pode ser vinculado se não tiver outra identidade externa. Correspondências ambíguas são rejeitadas.
- Uma Activity comum com mesmo nome e categoria não é convertida automaticamente: vincule Movie manualmente e reimporte. Isso evita converter uma tarefa normal por coincidência de título.
- Campos opcionais vazios preservam duração, pôster e URL existentes; para limpar um campo use o Admin. Importação não reativa registros, altera duração de bloco, premium ou progresso de assistidos.
- O save normal de Movie reconcilia filas conforme a SPEC-018. Itens futuros podem ser retirados; sessões abertas e histórico são preservados. A prévia simula isso em transação e faz rollback.
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

Os testes cobrem prévia sem persistência, carga de 97 registros, reimportação preservando progresso/metadata/inatividade, rollback de arquivo inválido, categoria incompatível, vínculo manual, proteção de Activity comum, cabeçalhos/anos, duas edições no mesmo ano e upload/confirmação/permissões no Admin.

Nenhuma carga no banco real foi executada nesta tarefa. Antes de importar, revise a prévia e eventuais rótulos já existentes. Importações simultâneas podem gerar conflito de unicidade e rollback; execute uma confirmação por vez.

Validações desta entrega: doctor aprovado; 31 testes de importação/contratos SPEC-018 aprovados; suíte completa de 293 testes aprovada, com 20 skips de testes dependentes de PostgreSQL; system check sem problemas; makemigrations sem mudanças; poetry check --lock aprovado com avisos preexistentes de metadados deprecados; versões anteriores do lockfile preservadas; CSV com edições 1–97 únicas; git diff --check sem erros. PostgreSQL não foi usado nesta tarefa: não houve mudança nas regras de locks do sorteio.
