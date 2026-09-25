# Handoff — importação do catálogo RetroGames

## Entrega

Implementada a SPEC-BACK-016 com catálogo JSON versionado, serviço de validação,
planejamento somente leitura, importação atômica/idempotente, comparação opcional com
inventário e comando Django com relatórios humano e JSON.

O catálogo possui 8 gerações, 23 plataformas e 116 jogos. O inventário local confirmou
97 nomes diretamente e 19 por aliases explícitos. A fonte declara 991 h no resumo, mas
as linhas somam 1001 h; o importador valida 1001 h e emite aviso.

## Segurança operacional

A importação real não foi executada no banco configurado da aplicação. Somente um
SQLite isolado em `tests/.tmp/` foi migrado para dry-run e comparação. Antes do deploy,
confirme explicitamente host, nome e ambiente do banco e execute primeiro o dry-run.

`--deactivate-missing` afeta somente Activity/RetroGame identificados por
`external_source=retrogames_catalog`. Plataformas órfãs são mantidas porque o modelo não
registra sua proveniência.

## Operação sugerida

```bash
python manage.py import_retrogames_catalog --dry-run --format=json
python manage.py import_retrogames_catalog
```

Use `--inventory-file` apenas em uma máquina que possua o inventário original. Consulte
`docs/specs/SPEC-BACK-016_IMPORTACAO_CATALOGO_RETROGAMES.md` para contrato, rollback,
limitações e política de campos.

