# Importação de atividades da Steam pelo Admin

## Objetivo

Importar a biblioteca da conta Steam configurada como atividades do Pomodoro Task por uma
operação administrativa explícita, idempotente e protegida por autenticação, permissões e CSRF.

## Variáveis de ambiente

```env
STEAM_API_KEY=
STEAM_ID64=76561198065747727
STEAM_ACTIVITY_CATEGORY_ID=21
STEAM_ACTIVITY_DEFAULT_DURATION=60
```

`STEAM_API_KEY` é obrigatória. As demais variáveis usam os valores acima como padrão. A chave
é lida somente da configuração do servidor e nunca deve aparecer em logs, HTML, migrations ou
commits.

## Fluxo administrativo

Em `Admin > Pomodoro > Activities`, o botão **Importar jogos da Steam** envia um formulário
`POST` com CSRF. A rota exige usuário administrativo com permissões de inclusão e alteração de
`Activity`. O serviço valida configuração e categoria, consulta a Steam com timeout e devolve os
contadores de encontrados, criados, atualizados, ignorados e erros.

O cliente usa `urllib` da biblioteca padrão com validação SSL normal e timeout explícito. Não
foi adicionada dependência HTTP ao projeto, portanto `pyproject.toml` e `poetry.lock` permanecem
inalterados.

## Mapeamento Steam para Activity

Na criação, o nome e o AppID vêm da Steam; a descrição registra a origem e o AppID. A categoria
e a duração vêm das configurações. `active=True`, `premium=False`, `executions_today=0` e
`priority=1`. Datas premium não são preenchidas.

## Idempotência e política de atualização

Foi adotado o par genérico `external_source` + `external_id`, protegido por unicidade condicional
quando ambos estão preenchidos. Para a Steam, os valores são `steam` e o AppID decimal. Essa
alternativa evita acoplar `Activity` a um provedor e permite futuras integrações sem novos campos.

Em sincronizações posteriores, apenas nome, descrição de origem e categoria são controlados. A
categoria é restaurada para o ID configurado, porque ela define o agrupamento funcional desta
importação. Duração, prioridade, `active`, premium e campos de execução não são sobrescritos.

## Categoria e transações

A categoria, `21` por padrão, precisa existir antes da consulta à Steam; ela não é criada
automaticamente. Cada jogo usa uma transação própria contendo a persistência e a reconciliação
das filas. Se uma reconciliação falhar, aquele jogo é revertido e contabilizado como erro, sem
invalidar itens importados com sucesso.

## Testes

```bash
.venv/bin/poetry run python manage.py test apps.pomodoro.test_steam_import
.venv/bin/poetry run python manage.py test
```

Os testes usam SQLite isolado e mockam integralmente o acesso HTTP; nenhuma chamada real à Steam
é executada.

## Riscos e limitações

- A importação é síncrona e processa todos os jogos devolvidos pela API, sem limite artificial;
  bibliotecas muito grandes aumentam o tempo da requisição administrativa.
- Itens sem AppID/nome válido ou com nome acima do limite do model são contabilizados como erro.
- Renomear manualmente um jogo importado será desfeito na sincronização seguinte.
- A chave da Steam usada fora deste fluxo deve ser rotacionada se houver suspeita de exposição.
