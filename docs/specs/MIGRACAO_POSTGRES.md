# Migração do SQLite para PostgreSQL

## Status

- Tipo: spec/plano.
- Estado: implementação local em andamento; deploy na VPS pendente.
- Escopo: desenvolvimento local, testes, migração dos dados existentes e deploy Docker na VPS.
- Banco de origem: SQLite em `db.sqlite3`.
- Banco de destino: PostgreSQL 16 central da VPS.

## Objetivo

Migrar o backend Django do SQLite para PostgreSQL sem perder os dados existentes, mantendo testes isolados e conectando o deploy ao PostgreSQL central pela rede Docker privada `backend_net`.

## Estado atual

### Aplicação

- Python 3.12 e Django 5.2, gerenciados por Poetry.
- Configuração de banco centralizada em `config/settings/base.py`.
- SQLite configurado como banco padrão.
- Não há driver PostgreSQL nas dependências.
- Não há `Dockerfile`, `compose.yml` da aplicação ou `.env.example`.
- O deploy atual usa Gunicorn, Supervisor e Nginx, sem container da aplicação.
- O Gunicorn atual inicia com `config.settings.local`, mesmo no fluxo de serviço.

### PostgreSQL central local

- O stack de desenvolvimento já existe em `/home/barscka/workspace/postgres`.
- O serviço usa a imagem `postgres:16` e o nome de container `postgres`.
- A porta está publicada somente em `127.0.0.1:5432`.
- Os dados persistem no volume nomeado `postgres_data`.
- O container participa da rede Docker externa `backend_net`.
- O servidor está ativo e aceitando conexões.
- Não será criado outro PostgreSQL dentro deste projeto.
- O banco e o usuário de desenvolvimento do Pomodoro ainda devem ser criados explicitamente nesse cluster local.

### Banco SQLite

- Tamanho observado: 276 KB.
- `PRAGMA integrity_check`: `ok`.
- `PRAGMA foreign_key_check`: nenhuma inconsistência.
- O banco possui dados de domínio, um usuário Django e uma API key.
- As migrations `pomodoro.0007` a `pomodoro.0010` ainda não foram aplicadas no SQLite existente.
- O código atual já depende dos campos criados por essas migrations.

Contagens observadas antes da migração:

| Tabela lógica | Registros |
| --- | ---: |
| Usuários Django | 1 |
| API keys | 1 |
| Atividades | 22 |
| Categorias | 6 |
| Agendamentos | 7 |
| Históricos | 7 |
| Perfis | 1 |
| Hard skills | 6 |
| Soft skills | 5 |
| Idiomas | 2 |
| Itens de portfólio | 3 |
| Experiências profissionais | 3 |

### Validação atual

- `python manage.py check`: passou.
- `python manage.py makemigrations --check --dry-run`: passou.
- `python manage.py test pomodoro user_profile`: 6 testes passaram.
- `python manage.py test`: falha na descoberta automática porque `apps.pomodoro` é resolvido em conflito com o módulo `apps` inserido no `sys.path`.

O problema de descoberta deve ser corrigido antes da migração para que o comando padrão da suíte seja uma evidência confiável.

## Decisões de arquitetura

### Desenvolvimento local

Reutilizar o PostgreSQL central local já executado por `/home/barscka/workspace/postgres/compose.yml`. Como o Django atualmente roda diretamente no host, a aplicação deve conectar em `127.0.0.1:5432`.

Cada projeto deve possuir banco e usuário próprios dentro desse cluster. Para este backend, usar:

```text
Banco: pomodoro_task_dev
Usuário: pomodoro_task_dev_user
```

O backend não deve criar ou gerenciar o container PostgreSQL local. Sua documentação deve apenas explicar como validar o stack central e como criar o banco e o usuário específicos da aplicação.

### Testes automatizados

Manter SQLite isolado nos testes, por meio de settings próprios e ambiente explicitamente marcado como teste. A suíte não pode usar `DATABASE_URL` ou credenciais de desenvolvimento/produção como fallback.

### Deploy

Containerizar o backend e conectá-lo à rede externa `backend_net`. O container acessará o PostgreSQL central por `postgres:5432`.

O compose da aplicação não criará outro PostgreSQL e não publicará a porta 5432. O backend poderá expor sua porta apenas em `127.0.0.1` para o Nginx do host, se o proxy continuar fora do Docker.

### Credenciais

Criar banco e usuário exclusivos para a aplicação:

```text
Banco: pomodoro_task_prod
Usuário: pomodoro_task_user
```

O usuário administrativo do PostgreSQL central não será usado pela aplicação. Senhas reais ficarão somente no `.env` não versionado da VPS.

### Transferência de dados

Usar serialização do Django para transportar dados entre engines, depois de atualizar uma cópia do SQLite para o estado atual das migrations.

Serão transferidos:

- dados de `pomodoro`;
- dados de `user_profile`;
- usuários Django necessários;
- API keys, preservando os hashes existentes.

Não serão transferidos por padrão:

- histórico de migrations;
- `django_content_type` e permissões geradas, pois serão recriados pelas migrations;
- sessões ativas;
- logs do Django Admin;
- estado e histórico do scheduler, atualmente vazios.

## Arquivos previstos

| Arquivo | Alteração planejada |
| --- | --- |
| `pyproject.toml` | Adicionar o driver `psycopg` compatível com o projeto. |
| `poetry.lock` | Registrar a resolução da nova dependência. |
| `config/settings/base.py` | Remover o SQLite como banco operacional e centralizar leitura segura das variáveis de banco. |
| `config/settings/local.py` | Configurar desenvolvimento com PostgreSQL local. |
| `config/settings/production.py` | Configurar PostgreSQL central e falhar quando variáveis obrigatórias estiverem ausentes. |
| `config/settings/test.py` | Isolar testes em SQLite sem fallback para banco real. |
| `config/settings/__init__.py` | Tornar explícito o comportamento dos settings padrão, sem seleção silenciosa de produção. |
| `.env.example` | Documentar variáveis sem credenciais reais. |
| `.gitignore` | Ignorar artefatos temporários da migração e banco de teste. |
| `.dockerignore` | Excluir ambiente virtual, SQLite, segredos e artefatos locais da imagem. |
| `Dockerfile` | Criar imagem do backend Django/Gunicorn. |
| `compose.yml` | Executar o backend na VPS conectado à `backend_net`, sem serviço PostgreSQL. |
| `README.md` | Atualizar instalação, execução, migrations, testes e deploy. |
| Testes/settings | Corrigir descoberta da suíte e cobrir proteção contra banco real em testes. |

Não serão criadas novas migrations de modelos apenas para trocar o engine do banco. As migrations existentes devem criar o mesmo schema no PostgreSQL.

## Variáveis de ambiente

Modelo para desenvolvimento:

```env
APP_ENV=development
TZ=America/Sao_Paulo
DB_HOST=127.0.0.1
DB_PORT=5432
DB_NAME=pomodoro_task_dev
DB_USER=pomodoro_task_dev_user
DB_PASS=troque_esta_senha
```

Modelo para deploy:

```env
APP_ENV=production
TZ=America/Sao_Paulo
DB_HOST=postgres
DB_PORT=5432
DB_NAME=pomodoro_task_prod
DB_USER=pomodoro_task_user
DB_PASS=troque_esta_senha
```

Para testes:

```env
APP_ENV=test
TESTING=true
TEST_DATABASE_URL=sqlite:///tests/.tmp/app_test.sqlite
```

O projeto deve interromper a inicialização quando `APP_ENV=production` e qualquer variável obrigatória de banco estiver ausente.

## Plano de execução

### Fase 1 — Preparar baseline confiável

1. Corrigir a descoberta para que `python manage.py test` execute toda a suíte.
2. Criar settings exclusivos de teste com SQLite isolado.
3. Adicionar teste que impeça o uso acidental do banco de desenvolvimento ou produção durante a suíte.
4. Executar `check`, verificação de migrations e testes antes de alterar o banco operacional.

Critério de saída: comando padrão de testes passando e sem conexão com PostgreSQL real ou compartilhado.

### Fase 2 — Preparar suporte ao PostgreSQL

1. Adicionar `psycopg` pelo Poetry.
2. Refatorar os settings para usar variáveis separadas de banco.
3. Remover `TIME_ZONE` específico da conexão SQLite; manter `TIME_ZONE = "America/Sao_Paulo"` e `USE_TZ = True` no Django.
4. Criar `.env.example` sem segredos.
5. Validar o stack existente em `/home/barscka/workspace/postgres`, sem alterar seu compose ou volume.
6. Criar `pomodoro_task_dev_user` e `pomodoro_task_dev` no cluster local, com permissões restritas ao banco da aplicação.
7. Configurar o Django local para `127.0.0.1:5432`.
8. Executar todas as migrations no banco local vazio.

Critério de saída: aplicação inicializa, migrations executam do zero e testes de integração passam com PostgreSQL local.

### Fase 3 — Containerizar a aplicação

1. Criar imagem mínima do backend.
2. Executar migrations como etapa explícita de release, sem múltiplos workers tentando migrar simultaneamente.
3. Criar compose de produção apenas com a aplicação.
4. Anexar o serviço à `backend_net` externa.
5. Configurar Gunicorn com `config.settings.production`.
6. Manter Nginx acessando o backend por uma porta ligada somente a `127.0.0.1`, se Nginx continuar no host.

Critério de saída: container resolve o host `postgres`, alcança a porta 5432 e responde ao health check sem expor o banco.

### Fase 4 — Ensaio da migração de dados

1. Copiar `db.sqlite3` para uma área temporária ignorada pelo Git.
2. Calcular e registrar SHA-256 da cópia original.
3. Executar `integrity_check` e `foreign_key_check` na cópia.
4. Aplicar as migrations pendentes na cópia, nunca no único arquivo de origem sem backup.
5. Validar a migration de dados que cria o grupo padrão e vincula categorias existentes.
6. Exportar somente os modelos definidos nesta spec.
7. Criar um PostgreSQL descartável vazio.
8. Executar `migrate` no destino.
9. Importar a fixture.
10. Confirmar ajuste das sequences após a carga.
11. Comparar contagens, maiores PKs, valores nulos, unicidade e relacionamentos.
12. Exercitar endpoints de listagem, próxima atividade, início, conclusão e histórico.
13. Validar a API key existente sem revelar seu valor.

Critério de saída: ensaio reproduzível, sem divergência de dados e com todos os contratos principais funcionando.

### Fase 5 — Preparar PostgreSQL central

1. Confirmar que o container PostgreSQL central está saudável.
2. Confirmar existência da rede `backend_net`.
3. Criar `pomodoro_task_user` com senha forte.
4. Criar `pomodoro_task_prod` tendo o usuário da aplicação como proprietário.
5. Conceder apenas os privilégios necessários sobre o banco e schema `public`.
6. Configurar o `.env` da aplicação com permissão restrita.
7. Fazer backup do PostgreSQL central antes do deploy.

Nenhum comando destrutivo, alteração de credencial administrativa ou exposição de porta será executado sem autorização explícita.

### Fase 6 — Cutover

1. Abrir uma janela curta de manutenção e interromper escritas no SQLite.
2. Parar o processo antigo do backend.
3. Copiar o SQLite final, calcular checksum e preservar o original sem alterações.
4. Atualizar a cópia final com as migrations pendentes.
5. Gerar a fixture final usando o mesmo procedimento validado no ensaio.
6. Executar migrations no PostgreSQL de produção vazio.
7. Importar os dados e validar sequences.
8. Comparar as contagens finais com a origem.
9. Subir o backend containerizado.
10. Validar logs, conexão, autenticação e endpoints críticos.
11. Liberar tráfego somente depois de todos os critérios de aceite passarem.

### Fase 7 — Observação e encerramento

1. Observar logs de aplicação e PostgreSQL após o corte.
2. Confirmar que novos registros usam IDs posteriores aos importados.
3. Confirmar comportamento de datas na virada do dia em `America/Sao_Paulo`.
4. Manter o SQLite original e os artefatos de migração fora do Git durante o período de retenção definido.
5. Documentar operação, backup e restauração no README ou runbook do projeto.

## Validações obrigatórias

### Código e schema

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py migrate --plan
python manage.py test
```

Também deve ser validada a criação integral do schema em PostgreSQL vazio, sem usar `--fake`.

### Dados

- Contagem por modelo igual entre origem e destino.
- PK máxima igual para os modelos importados.
- Nenhuma FK órfã.
- Unicidade de categorias, grupos, agendamentos e API keys preservada.
- Grupo padrão criado e todas as categorias associadas.
- Usuário Django consegue autenticar.
- API key existente continua válida.
- Datas e horários mantêm o instante correto após serialização.
- Próximos inserts não colidem com IDs importados.

### Infraestrutura e segurança

- O container da aplicação está na `backend_net`.
- `postgres` resolve por DNS interno.
- A aplicação alcança `postgres:5432`.
- A porta 5432 não está publicada no host.
- Nenhum segredo aparece em imagem, compose versionado, logs ou documentação.
- A aplicação usa `pomodoro_task_user`, não o usuário administrativo.
- O `.env` da VPS possui permissão restrita.

## Critérios de aceite

- Todas as migrations executam em PostgreSQL vazio.
- Suíte padrão completa passa.
- Dados selecionados são migrados sem divergências.
- Autenticação por API key e endpoints críticos funcionam.
- Deploy reinicia sem perder conexão ou dados.
- PostgreSQL permanece inacessível pela rede pública.
- Backup e rollback foram verificados antes da liberação.

## Riscos e mitigação

| Risco | Mitigação |
| --- | --- |
| Perda de dados ao aplicar migrations pendentes | Trabalhar primeiro em cópia, preservar origem e registrar checksum. |
| Diferenças de coerção entre SQLite e PostgreSQL | Criar schema por migrations e ensaiar carga integral em PostgreSQL descartável. |
| Datas mudarem de dia por timezone | Validar registros próximos à meia-noite com `USE_TZ=True` e timezone local. |
| Sequences ficarem abaixo das PKs importadas | Resetar e validar sequences antes de liberar escritas. |
| API key deixar de funcionar | Incluir o modelo de API key e executar teste autenticado após a carga. |
| Testes atingirem banco real | Settings exclusivos, marcador de ambiente e bloqueio explícito de hosts/URLs não permitidos. |
| Migrations concorrentes no deploy | Executar uma etapa única de release antes de iniciar workers. |
| Backend do host não alcançar PostgreSQL privado | Containerizar e conectar à `backend_net`; não publicar 5432. |
| Projeto criar um segundo PostgreSQL local | Reutilizar exclusivamente o stack central em `/home/barscka/workspace/postgres`. |
| Rollback perder escritas feitas no PostgreSQL | Manter janela sem escrita até aceite; após liberação, preferir correção progressiva. |

## Rollback

### Antes de liberar novas escritas

1. Parar o container novo.
2. Manter o PostgreSQL intacto para diagnóstico.
3. Restaurar o processo anterior do backend.
4. Reapontar o serviço para o SQLite original preservado.
5. Validar autenticação e endpoints antes de reabrir tráfego.

### Depois de liberar novas escritas

Retornar diretamente ao SQLite causaria perda das alterações realizadas no PostgreSQL. Nesse estágio, o padrão é corrigir o problema no PostgreSQL e avançar. Uma reversão com transporte de dados de volta exigirá novo plano, nova janela de manutenção e autorização explícita.

Não fazem parte do rollback automático: apagar banco, usuário, schema, volume Docker ou backups.

## Fora do escopo

- Upgrade de Python, Django ou demais dependências além do driver PostgreSQL.
- Refatoração das regras de negócio.
- Alteração do stack PostgreSQL/pgAdmin central.
- Exposição pública do PostgreSQL.
- Migração automática de sessões e logs operacionais descartáveis.
- Exclusão definitiva do SQLite ou dos backups imediatamente após o corte.

## Pendências antes do deploy na VPS

- Confirmar os nomes de produção `pomodoro_task_prod` e `pomodoro_task_user`.
- Definir duração da janela de manutenção e período de retenção do SQLite.
- Confirmar se Nginx continuará no host ou também será containerizado em tarefa separada.

## Evidências da implementação local

- Banco `pomodoro_task_dev` e usuário dedicado criados no PostgreSQL central local.
- Driver `psycopg` adicionado pelo Poetry.
- Settings separados para desenvolvimento, produção, testes e cópia SQLite legada.
- Testes automatizados isolados em SQLite.
- Descoberta padrão corrigida para executar toda a suíte.
- Schema PostgreSQL criado integralmente pelas migrations existentes.
- SQLite original preservado; migrations pendentes aplicadas apenas em cópia temporária.
- 64 objetos de aplicação e autenticação importados no PostgreSQL local durante o ensaio.

As fases de containerização e cutover na VPS permanecem pendentes e não devem começar antes das decisões registradas nesta spec.
