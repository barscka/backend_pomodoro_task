# Handoff — associação automática de sessões à rotina

Data: 2026-10-04. Branch: codex/sessoes-na-agenda.
Backend implementado e validado; integração Flutter, implantação e push não realizados.

## Padrões carregados

AGENTS.md, .personal-skills.json e configuração local. Repositório pessoal resolvido
por local_path; doctor passou com aviso de PERSONAL_SKILLS_HOME não definida.
Carregados personal-python-api e personal-dev-workflow, com referências exigidas:
django, fastapi, flask, poetry, python_api, python_database_safety,
python_project_structure, python_tests, python_upgrade, git, commits e engenharia_ia.
Preservados services/repositories, transporte fino, testes isolados e contratos aditivos.

## Investigação e decisão

Os três services canônicos criam Schedule/History dentro de transação; os retries
retornam execuções existentes e complete_schedule grava GoalCompletion preciso.
O resumo da SPEC-017 usa esses facts para interseções civis, sem vínculo persistido.
A expansão já resolve revisões pela data de origem, exceções e suspensão civil.

Foi acrescentado RoutineSessionAssociation, único por execução, independente do plano
 e catálogo. Seus dados são evidência do instante original de início. Não se usa
horário do retry, clique em preferência ou término para escolher o bloco.

Todos os starts travam o plano existente antes de item/período/jogo, na mesma ordem
usada por routines/start. Não criam plano. Depois da criação da nova execução,
resolvem o gameplay ativo em Schedule.starts_at e persistem o vínculo na mesma
transação. Retries, recuperação, conclusão e GETs nunca criam ou recalculam vínculos.
O plano observado no começo do start é usado; ausência de plano não vira vínculo
quando outro dispositivo cadastra um plano durante essa operação.

## Campos e exemplos para Flutter

Contrato completo: [SPEC-017](../contracts/spec-017-routines.md), extensão
“Sessões associadas automaticamente”. Novas fixtures:
[spec-017-routine-sessions.json](../contracts/spec-017-routine-sessions.json), cinco
exemplos golden; as 17 fixtures anteriores também foram atualizadas.

Execuções retornam campos adicionais:

- routine_occurrence: snapshot do bloco realmente ativo, ou null.
- routine_requested_occurrence: identidade da preferência enviada a routines/start,
  ou null nos inícios pelos módulos diretos. Pode ser diferente do vínculo real.

Snapshot: occurrence_id, block_id, origin_date, revision_version, timezone, kind,
profile, starts_at e ends_at. A data de origem é a data local em que o bloco começa,
mesmo quando a sessão começa depois da meia-noite.

Agenda mantém occurrences/days/version/timezone e acrescenta:

- as_of: instante do servidor usado para estimativas.
- occurrences[].sessions: sessões da ocorrência atual.
- occurrences[].sessions_pagination: page/page_size/count/has_next.
- occurrences[].session_totals: confirmed_seconds/open_estimate_seconds.
- recorded_occurrences: grupos de vínculos históricos, presentes mesmo sem bloco
  atual após cancelamento/movimento/suspensão ou remoção do plano.

Cada grupo registrado tem occurrence_id/block_id/origin_date, snapshots, sessions,
sessions_pagination e session_totals. snapshots preserva os intervalos vigentes nos
inícios; pode haver vários snapshots do mesmo bloco se ajustes ocorreram entre sessões.

Sessão: execution_id, activity_id, activity_name, execution_origin, state, starts_at,
ends_at, expected_end_at, confirmed_seconds, open_estimate_seconds, coverage,
routine_occurrence e routine_requested_occurrence.

Exemplo conceitual, com identidades abreviadas; consulte fixtures para objetos completos:

```json
{
  "occurrence_id": "2026-10-10:UUID_DO_BLOCO",
  "origin_date": "2026-10-10",
  "sessions": [{
    "execution_id": 601,
    "activity_id": 101,
    "activity_name": "Jogo manual",
    "execution_origin": "premium_direct",
    "state": "completed",
    "starts_at": "2026-10-10T23:37:00-03:00",
    "ends_at": "2026-10-11T01:37:00-03:00",
    "confirmed_seconds": 7200,
    "open_estimate_seconds": 0,
    "coverage": "complete"
  }],
  "session_totals": {"confirmed_seconds": 7200, "open_estimate_seconds": 0}
}
```

Uma sessão de domingo às 00h15 também se associa a esse bloco de sábado; às 01h00,
fora do intervalo exclusivo, routine_occurrence é null. O timer continua funcionando.

## Semântica de associação e contagem

- Gameplay: origens premium_direct/retro_direct; ou fila de grupo explicitamente
  configurado para acompanhamento; ou sessão de fila vinculada a período Premium.
  Nome do grupo, tela Foco e classificação de adequação não tornam estudo/trabalho jogo.
- Só bloco gameplay ativo, após exceções/suspensão. Nunca associar ao próximo bloco.
- Revision vigente é a do dia de origem. Uma revisão nova no domingo não reescreve
  a ocorrência de sábado que termina domingo às 01h00.
- Snapshot guarda identidade, intervalo efetivo, revisão, fuso, perfil, atividade e
  origem. Ajustes futuros não atualizam esse snapshot.
- Sem histórico suficiente não existe backfill. Migration 0022 é somente estrutural.
- O vínculo tem source_schedule_id única e FK opcional para Schedule, com SET_NULL.
  Mesmo removendo plano, execução ou catálogo, os dados do vínculo sobrevivem.
  GoalCompletion preciso, quando existe, mantém o confirmado após remoção da execução.
- confirmed_seconds vem do fact preciso e representa a sessão inteira, inclusive
  ultrapassagem do bloco, sem transformar reserva planejada em realização.
- open_estimate_seconds fica separado, limitado a as_of/expected_end_at; um GET não
  reconcilia execução atrasada nem confirma tempo.
- Sem fact preciso, confirmado é zero e coverage pode ser insufficient. Após apagar
  execução sem fact, state é unavailable. Estados cancelados/expirados não geram
  confirmado artificial.
- Resumo civil não foi alterado: usa facts/interseções, não o vínculo. Para 23h37–01h37:
  agenda da origem sábado = 120min registrados; resumo sábado = 23min dentro;
  domingo = 60min dentro + 37min fora. Nenhuma duplicação ou cumprimento integral.

## Mudanças necessárias no Flutter

1. Acrescentar os campos opcionais aos modelos de execução, mantendo compatibilidade
   com respostas anteriores sem esses campos. Inícios Premium/Retro/fila continuam
   enviando os payloads existentes, sem dia/bloco.
2. Ler sessões e totais da agenda e renderizar jogo, origem, estado, início/fim,
   confirmado e estimativa aberta separadamente. Timer continua no controller atual.
3. Unir occurrences e recorded_occurrences por occurrence_id. Deduplicar sessões
   por execution_id. As duas listas expõem os mesmos vínculos; não somar ambas.
4. Exibir registros históricos com snapshots quando o bloco atual foi removido,
   mantendo rótulo de data de origem. Se o ID ainda existe mas os horários atuais
   diferem, mostrar o snapshot da sessão como histórico, sem movê-la visualmente
   para o intervalo editado. Não reconstruir vínculo pelo relógio do cliente.
5. Reconsultar agenda após início, conclusão/reconciliação e recuperação canônica.
   Durante sessão aberta, atualizar estimativa por as_of ou reler GET; não apresentar
   estimativa como confirmado nem executar conclusão a partir da agenda.
6. Para mais sessões, enviar sessions_page (padrão 1, tamanho fixo 50 por ocorrência).
   count/totais são de todas as sessões, não só da página; não somar totais das páginas.
7. Quando preferência e ocorrência ativa diferirem, apresentar a preferência como
   contexto de escolha e o vínculo real como registro de início. Não mover a sessão
   para o bloco preferido depois.
8. Não chamar activities/next ao abrir agenda; leitura continua sem apresentação,
   consumo ou recriação de fila. Não adicionar session_totals ao resumo civil.

Consulta inclui snapshots que cruzam o intervalo civil OU origin_date dentro dele.
Uma consulta só de domingo pode mostrar a ocorrência de sábado que atravessa esse dia;
os detalhes da sessão são integrais, sem recorte civil. O resumo continua recortado.

## Arquivos alterados

- models.py e migration 0022_schedule_routine_requested_occurrence_and_more.py:
  associação persistida, constraint de início dentro do intervalo, índice por
  scope/origin_date/block_id e contexto da preferência em Schedule.
- services/activity_execution.py, premium_execution.py, retro_execution.py:
  lock do plano antes dos locks existentes e associação somente na criação.
- services/routine_sessions.py: elegibilidade, snapshot e apresentação da agenda.
- repositories/routines.py: consultas por escopo, intervalo e facts em lote.
- services/routine_selection.py: preferência separada do bloco ativo no start novo.
- serializers.py: campos aditivos em ActivityExecutionSerializer.
- routine_serializers.py/routine_views.py: sessions_page e agenda enriquecida.
- test_routine_sessions.py/test_routine_sessions_postgres.py: casos de associação,
  agenda, regressão e concorrência.
- docs/contracts/spec-017-routines.md/.json e spec-017-routine-sessions.json:
  extensão do contrato, exemplos e fixtures golden.
- Postman: consulta de agenda com sessions_page e explicação dos totais.
- README.md, SPEC-017 e este handoff: entrega e pendências.

Nenhum arquivo Flutter ou serviço de conclusão/contagem civil foi alterado.

## Validações

- Doctor: passou.
- check: sem problemas; makemigrations --check --dry-run: nenhuma mudança pendente.
- git diff --check: passou. JSONs e golden fixtures: válidos em SQLite/PostgreSQL.
- SQLite isolado: 256 testes, 242 passaram e 14 exclusivos de PostgreSQL ignorados.
- PostgreSQL 16 descartável: 33 testes, todos passaram (22 casos novos de associação,
  dois novos de concorrência e nove de regressão de locks das rotinas/Premium/Retro).
- Migration aplicada apenas pelos runners de teste, nos bancos isolados.

Cobertos: sábado 23h37, domingo 00h15, limite 01h00, timestamps UTC, revisão na virada,
overrun e recorte civil, sem plano/livre/família, suspensão, exceção/cancelamento,
revisão futura, scope, jogos de fila configurados/período Premium, fila não gameplay,
inícios Premium/Retro/fila, recuperação, retry fora da janela e concorrente,
preferência diferente da ocorrência real, persistência histórica e paginação.

Comando SQLite:

```sh
APP_ENV=test TEST_DATABASE_URL=sqlite:///tests/.tmp/routine_sessions.sqlite \
DJANGO_SETTINGS_MODULE=config.settings.test .venv/bin/python manage.py test --noinput
```

PostgreSQL: criado para esta task com tmpfs, nome codex-routine-sessions-pg-test,
porta local 55438, credenciais artificiais, sem volume persistente; removido após
validação. Nunca apontar o perfil de teste a banco real/compartilhado.

```sh
TESTING=true TEST_POSTGRES_DB=routine_sessions_test TEST_POSTGRES_USER=routine_test \
TEST_POSTGRES_PASSWORD=isolated_test_only TEST_POSTGRES_HOST=127.0.0.1 \
TEST_POSTGRES_PORT=55438 DJANGO_SETTINGS_MODULE=config.settings.postgres_concurrency \
.venv/bin/python manage.py test apps.pomodoro.test_routine_sessions \
apps.pomodoro.test_routine_sessions_postgres apps.pomodoro.test_spec_017_postgres \
apps.pomodoro.test_spec_014_postgres apps.pomodoro.test_spec_015_postgres --noinput
```

Logs RuntimeError: boom na suíte completa são falhas simuladas dos testes existentes.

## Riscos e passos pendentes

- Aplicar migration 0022 no ambiente autorizado antes de publicar o código que lê
  os novos campos; nenhuma migration real foi executada nesta task.
- Integrar os campos opcionais e atualização da agenda no Flutter e homologar.
- Configure grupos de gameplay explicitamente para filas comuns; não haverá vínculo
  para grupos não escolhidos. Preferência no bloco não substitui essa configuração.
- Sem vínculo em registros antigos ou starts fora de gameplay. Não foi criado comando
  de backfill. Ausência do vínculo não significa ausência de gameplay registrado.
- Leitura não reconcilia sessão atrasada: estado/estimativa só se tornam confirmados
  após o fluxo canônico existente. Não há pausa, encerramento por horário ou automação.
- Paginação limita a resposta, mas totais/snapshots são calculados sobre os vínculos
  do intervalo; homologar volume e latência no ambiente de destino.
- Não houve push, deploy, Flutter ou acesso a banco real. A migration foi testada
  em SQLite e PostgreSQL descartável; homologação integrada permanece pendente.
