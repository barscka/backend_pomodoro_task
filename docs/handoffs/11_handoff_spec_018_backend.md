# Handoff — SPEC-018 backend

Data: 2026-10-09. Branch: codex/spec-roleta-filmes-oscar.
Estado inicial: Git limpo. Frontend não alterado. Nenhuma dependência atualizada.

## Padrões carregados

AGENTS.md, .personal-skills.json e localização por .personal-skills.local.json.
Do repositório /home/barscka/workspace/skills/skills_pessoais:

- personal-python-api/SKILL.md;
- personal-python-api/references/django.md, fastapi.md, flask.md, poetry.md, python_api.md, python_database_safety.md, python_project_structure.md, python_tests.md e python_upgrade.md (arquivos exigidos pelo perfil);
- personal-dev-workflow/SKILL.md e referências git.md, commits.md e engenharia_ia.md indicadas por ele.

Doctor: aprovado; aviso de PERSONAL_SKILLS_HOME ausente, com caminho resolvido por configuração local. Nenhuma pasta de skill foi copiada para o projeto.

## Entrega

Catálogo manual no Admin, sete modelos de domínio e dois modelos internos de lock/idempotência, migração aditiva 0023, todos os endpoints da seção 6, catálogo/eventos paginados, progresso por identidade lógica, sorteio uniforme de população completa, snapshot e duração persistidos, recuperação por UUID, replay por payload_hash, versões/transações/locks, começar/concluir/desfazer/dispensar e reserva inválida recuperável.

Filmes excluídos de criação normal, recriação, revisão de pulados e promoção/reconciliação premium, inclusive Todos e Movie inativo. Conversão preserva execuções abertas e histórico. API de filmes não cria sessões/metas nem altera contadores Pomodoro. Eventos e registros operacionais são somente leitura no Admin.

Contratos definitivos, decisões e comandos seguros: docs/specs/SPEC-018_ROLETA_FILMES_OSCAR.md, seção 12.

## Arquivos alterados/criados

- apps/pomodoro/models.py
- apps/pomodoro/movie_models.py
- apps/pomodoro/migrations/0023_moviescopelock_movie_moviecollection_and_more.py
- apps/pomodoro/admin.py
- apps/pomodoro/serializers.py
- apps/pomodoro/views.py
- apps/pomodoro/movie_serializers.py
- apps/pomodoro/movie_views.py
- apps/pomodoro/urls.py
- apps/pomodoro/repositories/movies.py
- apps/pomodoro/services/movie_catalog.py
- apps/pomodoro/services/movie_draw.py
- apps/pomodoro/services/movie_progress.py
- apps/pomodoro/services/activity_queue.py
- apps/pomodoro/services/activity_queue_reconciliation.py
- apps/pomodoro/test_spec_018.py
- apps/pomodoro/test_spec_018_postgres.py
- scripts/test_spec018_postgres.sh
- docs/specs/SPEC-018_ROLETA_FILMES_OSCAR.md
- docs/handoffs/11_handoff_spec_018_backend.md

## Evidências de validação

- Doctor dos padrões pessoais: OK (aviso descrito acima).
- Django system check com config.settings.test: sem problemas.
- makemigrations --check --dry-run com config.settings.test: No changes detected.
- Suíte completa com APP_ENV=test, TEST_DATABASE_URL=sqlite:///tests/.tmp/spec018.sqlite, config.settings.test e --noinput: **284 testes, OK, 20 skips**. Skips incluem testes dependentes de PostgreSQL; SQLite não foi usado como evidência de locks.
- scripts/test_spec018_postgres.sh, versão final: **28 testes, OK** (22 funcionais/contratos/filas e 6 de concorrência real).
- PostgreSQL 16 novo e descartável: imagem já local, --pull=never, dados em tmpfs, porta efêmera em localhost, credenciais sintéticas, banco spec018_test e remoção automática pelo trap. Migrações aplicadas somente nesse banco e nos bancos criados pelo runner de testes SQLite.
- Concorrência PostgreSQL: primeiro sorteio com UUIDs diferentes; primeiro sorteio com mesmo UUID; criação inicial de progresso com estados incompatíveis; progresso existente com replay simultâneo; accept versus conclusão; mesmo UUID em duas coleções.
- Regras e contrato: população além da página, índices controlados, extremos 5000/10000 ms, separação lançamento/premiação e duração, zero/um candidato, watching/watched/undo, scope isolation, recuperação após timeout/desativação, replay/conflict, versões, rollback e nenhum efeito Pomodoro.
- Filas: grupos específicos/Todos, inatividade do Movie, premium, recriação com pulados, revisão de pulados, reconciliação da conversão, proteção de sessão aberta e preservação de histórico concluído.
- git diff --check: sem erros.

## Riscos e pendências

- Frontend, animação, acessibilidade e validação conjunta permanecem pendentes; esta task entrega somente backend.
- Cadastro editorial de coleção/categoria e filmes reais não realizado; nenhum serviço externo/lista histórica utilizado.
- Migração não aplicada em banco operacional/compartilhado. Implantação e revisão operacional continuam pendentes.
- A identidade é a build_scope_key existente. Credenciais compartilhadas podem compartilhar progresso.
- Mutações do mesmo escopo são serializadas entre coleções; custo cresce com quantidade de coleções. Adequado ao MVP, rever ao ampliar catálogo.
- Ledger e snapshots sem retenção automática. A resposta de replay é original; frontend deve buscar estado atual antes de nova mutação. GETs não garantem snapshot único entre todas as consultas; versões protegem as escritas.
