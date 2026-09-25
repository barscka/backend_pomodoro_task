---
spec_id: SPEC-BACK-016
titulo: Importação versionada do catálogo RetroGames
status: IMPLEMENTED
fase: AS_BUILT
criado_em: 2026-09-25
dependencias:
  - SPEC-BACK-015
---

# SPEC-BACK-016 — Importação versionada do catálogo RetroGames

## Objetivo e fonte

Substituir o cadastro manual do roteiro inicial de RetroGames por uma carga segura,
atômica e idempotente. A fonte de produção é o JSON versionado
`apps/pomodoro/data/retrogames_catalog.json`, convertido do planejamento curado de
2026-09-24. O importador nunca interpreta o Markdown nem importa o inventário bruto.

O catálogo usa `schema_version=1`, possui versão editorial explícita e contém 8
gerações, 23 plataformas e 116 jogos. As linhas individuais totalizam 60.060 minutos
(1001 h). O resumo original declara 991 h; essa divergência preexistente é preservada
em `source_declared_total_minutes`, documentada em `editorial_notes` e emitida como
aviso. O total validado pelo importador é sempre a soma das linhas.

## Formato e validação

Cada geração possui chave, nome, ordem e plataformas. Cada plataforma possui slug
global, metadados editoriais, ordem e jogos. Cada jogo possui chave global, nome, tier,
estimativa, duração do bloco, meta, ordem, estado e aliases opcionais.

Antes de qualquer consulta ao banco são validados: versão do schema, campos
obrigatórios, nomes, chaves e slugs únicos, ordens positivas e únicas no mesmo nível,
tier, anos entre 1970 e 2100, estimativas positivas, blocos de 1 a 720 minutos,
estrutura não vazia e total calculado. Erros indicam o caminho exato, por exemplo
`generations[0].platforms[0].games[0].estimated_main_minutes`.

## Identidade e campos gerenciados

- grupo: o único `Group.is_retro_catalog=true`; gerencia nome, descrição e cor;
- geração: nome dentro do grupo retrô; gerencia nome, descrição, cor e
  `retro_sort_order`;
- plataforma: `RetroPlatform.slug`; gerencia geração, nome, fabricante, ano, ordem e
  estado;
- Activity: `(external_source="retrogames_catalog", external_id=game.key)`; gerencia
  nome, descrição, duração, categoria, identidade e estado;
- RetroGame: relação 1:1 com a Activity gerenciada; gerencia plataforma, tier,
  estimativa, meta, ano, ordem, estado e capa.

O importador nunca busca um jogo apenas pelo nome. Assim, títulos homônimos em
plataformas diferentes não colidem. Prioridade, Premium e seus períodos, contadores,
progresso, History, Schedule, GoalCompletion, filas e timestamps históricos não são
campos gerenciados.

## Planejamento, idempotência e transação

`--dry-run` usa um planejador somente leitura: não abre transação de escrita, não cria
objetos, não dispara sinais e não avança sequences. A execução real usa uma única
`transaction.atomic()`, bloqueia o grupo e os registros identificados com
`select_for_update()` e depende também das constraints de unicidade. Qualquer falha
reverte a carga completa.

Somente campos divergentes são gravados. Uma segunda execução sem mudança retorna tudo
como inalterado e não chama `save()`. Uma Activity gerenciada movida manualmente volta
à geração/plataforma declarada; isso aparece como atualização no dry-run.

Itens removidos do JSON não são apagados. São relatados como `orphaned`. Com
`--deactivate-missing`, somente a Activity e o RetroGame cuja identidade externa prova
a origem no catálogo são desativados. Plataformas ausentes são mantidas e avisadas,
pois o modelo atual não possui campo de proveniência capaz de autorizar desativação
automática segura.

## Inventário local

`--inventory-file` é uma validação opcional e externa à importação. Ela ignora caminhos
de BIOS, firmware, saves, states, caches, configuração e extensões técnicas; normaliza
caixa, acentos, pontuação, região e extensão; e classifica cada jogo como confirmado,
confirmado por alias, possível ou não encontrado. Correspondência aproximada só gera
aviso: nunca cria, ativa ou renomeia dados.

Os aliases do catálogo documentam nomes regionais, títulos longos e identificadores de
sets arcade (por exemplo `mslug`, `garou` e `samsho2`). Na revisão inicial, os 116 jogos
foram confirmados no inventário: 97 pelo nome normalizado e 19 por alias explícito.

## Comandos e relatórios

```bash
python manage.py import_retrogames_catalog --dry-run
python manage.py import_retrogames_catalog --dry-run --format=json
python manage.py import_retrogames_catalog --dry-run --inventory-file /caminho/inventario.txt
python manage.py import_retrogames_catalog
python manage.py import_retrogames_catalog --deactivate-missing
```

A saída humana e a JSON apresentam criados, atualizados, inalterados, desativados e
órfãos para grupo, gerações, plataformas e jogos, além do resumo do inventário e
avisos. JSON inválido, schema incompatível ou violação estrutural encerra o comando com
código diferente de zero.

## Testes e rollback

A suíte dedicada cobre catálogo real, JSON/schema inválidos, ausências, duplicidades,
faixas, primeira e segunda importação, dry-run, atualização editorial, correção de
hierarquia, homônimos, proteção de Activity não gerenciada, preservação operacional,
órfãos, desativação, rollback total, inventário exato/alias/aproximado/técnico e ambos
os formatos de relatório. A constraint PostgreSQL de identidade externa e os locks da
carga são a defesa contra importações concorrentes; o teste PostgreSQL específico deve
ser executado apenas em banco descartável terminado em `_test`.

Rollback operacional: a carga não possui delete. Em erro, a transação reverte. Para
reverter uma versão editorial aplicada, restaure o JSON anterior e execute novamente;
itens retirados permanecem como órfãos, ou podem ser desativados explicitamente.

## Limitações e decisões editoriais

- não há proveniência persistida em `RetroPlatform`, portanto plataforma órfã não é
  desativada automaticamente;
- anos de jogos só são preenchidos quando a fonte ou a curadoria os tornou confiáveis;
- Paper Mario, Advance Wars e Dragon Quest VII usam metas reduzidas de amostra;
- Link's Awakening e Link's Awakening DX permanecem ativos, mas a fonte recomenda
  escolher uma versão;
- a comparação do inventário é uma verificação local opcional e não acompanha o deploy.

