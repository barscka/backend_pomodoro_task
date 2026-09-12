# Handoff — contexto e sinais das metas semanais

Data: 12/09/2026.

## Contrato implementado

O frontend pode continuar consumindo `GET /api/weekly-goals/progress/` com a
paginação e os campos existentes. Cada item agora inclui `destination` e os
contadores básicos de `activity_signals`. Use `include=activity_signals` quando a
tela realmente exibir a lista `most_skipped`; sem a expansão, a lista é omitida.

Exemplo de meta de categoria expandida:

```json
{
  "goal_id": 1,
  "metric": "minutes",
  "group_id": null,
  "category_id": 7,
  "destination": {
    "type": "category",
    "id": 7,
    "name": "Leitura",
    "color": "#4A90E2",
    "group_id": 3,
    "group_name": "Estudos"
  },
  "target": 240,
  "achieved": 45,
  "remaining": 195,
  "progress_percent": "18.75",
  "is_achieved": false,
  "activity_signals": {
    "skip_count": 3,
    "distinct_activities_skipped": 2,
    "most_skipped": [
      {"activity_id": 10, "name": "Revisar inglês", "skip_count": 2},
      {"activity_id": 15, "name": "Curso de programação", "skip_count": 1}
    ]
  }
}
```

Sem `include`, o mesmo objeto `activity_signals` contém somente `skip_count` e
`distinct_activities_skipped`. Meta sem pulos retorna ambos como zero; com a
expansão, `most_skipped` retorna `[]`.

Para meta de grupo, `destination.type` é `group`, `id`/`group_id` são o grupo e
`name`/`group_name` têm o mesmo nome. A cor é a cor atual do grupo. Para categoria,
a cor é a da categoria e o grupo pai vem em `group_id`/`group_name`. O grupo
“Todos” agrega todos os fatos do escopo e da semana.

`skip_count` conta ações; `distinct_activities_skipped` conta atividades únicas.
A mesma atividade pulada três vezes produz `3` e `1`. Os rótulos da lista são
snapshots do momento do pulo, portanto uma renomeação posterior não reescreve o
histórico. Não somar cards de categoria e grupo: o mesmo pulo pode aparecer nos
dois, assim como metas de conclusão sobrepostas.

## Erros, tempo e integração

- `include` aceita somente `activity_signals`, uma vez. Qualquer outro formato
  retorna `400` com `code=invalid_include`.
- A semana continua sendo a resposta canônica do servidor, de segunda 00:00 em
  `America/Sao_Paulo` até a segunda seguinte (fim exclusivo).
- Pulos não reduzem `achieved` e não classificam metas como atrasadas/em risco.
- O endpoint não altera filas nem reconcilia dados.
- Nenhum contador de cobertura da fila foi publicado: a semântica entre filas por
  grupo, “Todos” e revisão ainda precisa ser definida.
- Comparação entre semanas e distribuição deduplicada continuam reservadas para
  uma API futura; não devem ser simuladas somando os cards atuais.

## Implantação necessária antes do consumo histórico

Aplicar a migration `0018_goalactivityskip` no ambiente de destino e executar
primeiro `backfill_goal_activity_skips --dry-run`. A carga real é idempotente e não
sobrescreve fatos capturados ao vivo. Registros legados usam a classificação atual
disponível e vêm marcados internamente como `legacy_current`; essa qualidade não é
exposta no contrato atual.
