# Handoff - Migracao PostgreSQL e deploy Docker na VPS

## Objetivo

Registrar o estado apos a migracao do banco SQLite para PostgreSQL e a
ativacao do backend em Docker, sem armazenar credenciais, enderecos da VPS,
segredos, checksums ou conteudo de arquivos de ambiente.

Data da execucao: 2026-06-22.

## Estado final

- O backend e o Nginx da aplicacao executam pelo Docker Compose.
- Os dois servicos atingiram o estado `healthy` sem reinicializacoes durante
  a validacao final.
- O backend acessa o PostgreSQL central pela rede Docker privada
  `backend_net`.
- O PostgreSQL nao publica a porta `5432`.
- A aplicacao usa um papel PostgreSQL exclusivo, sem privilegios de
  superusuario, criacao de bancos ou criacao de papeis.
- O arquivo `.env` da VPS esta com permissao `600` e nao foi versionado.
- O segredo Django e a senha do papel da aplicacao foram renovados durante a
  execucao e nao foram registrados neste documento.
- Os caminhos do container foram ajustados para `/app/staticfiles` e
  `/app/media`.
- Foram coletados 215 arquivos estaticos no volume persistente.
- Nao existem migrations pendentes.

## Migracao dos dados

O SQLite original foi preservado sem alteracao. Antes da migracao foram
criados, fora do repositorio e em diretorio privado:

- copia preservada do SQLite;
- copia de trabalho do SQLite;
- backup do `.env` anterior;
- patch do estado Git anterior;
- dump do PostgreSQL antes da carga;
- fixture JSON usada na migracao;
- dump do PostgreSQL depois da carga.

A copia de trabalho passou nas verificacoes:

- `PRAGMA integrity_check`: `ok`;
- nenhuma violacao em `PRAGMA foreign_key_check`;
- todas as migrations do SQLite ja estavam aplicadas.

Dados confirmados no PostgreSQL apos a carga:

| Modelo | Registros | Maior PK numerica |
| --- | ---: | ---: |
| Usuarios | 1 | 2 |
| Grupos | 5 | 5 |
| Categorias | 18 | 18 |
| Atividades | 52 | 55 |
| Agendamentos | 107 | 136 |
| Historicos | 107 | 134 |

As sequences dos modelos de dominio foram reposicionadas para os maiores IDs
importados.

## Ocorrencias durante a execucao

### Fixture sem grupos

A primeira fixture continha categorias relacionadas a grupos, mas o comando
documentado nao exportava `pomodoro.group`. O PostgreSQL rejeitou a carga por
integridade referencial e o `loaddata` reverteu a tentativa de forma atomica.

A fixture foi regenerada incluindo `pomodoro.group` antes de
`pomodoro.category` e a nova carga instalou 313 objetos com sucesso.

O comando foi corrigido no `README.md` pelo commit:

```text
8136c3c docs: inclui grupos na exportacao da migracao
```

### Caminho antigo de arquivos estaticos

O `.env` ainda apontava o `STATIC_ROOT` para um caminho do host, indisponivel
no filesystem somente leitura do container. A configuracao operacional foi
alterada para os caminhos internos do container e o `collectstatic` passou.

### Health check insuficiente para o cutover

Antes das migrations, `/healthz/` retornava `200` porque valida apenas uma
consulta simples ao banco, enquanto a pagina principal retornava `500` por
falta de tabelas. Portanto, o health check deve ser combinado com validacao
de migrations e de pelo menos um endpoint funcional durante releases.

## Validacoes executadas

- `docker compose config --quiet`: passou.
- `python manage.py makemigrations --check --dry-run`: nenhuma mudanca.
- `python manage.py showmigrations --plan`: nenhuma migration pendente.
- `python manage.py check --deploy`: apenas quatro alertas relacionados ao
  deploy HTTP sem TLS.
- Pagina principal: HTTP `200`.
- Health check: HTTP `200`.
- Arquivo estatico pelo Nginx: HTTP `200`.
- Admin sem sessao: redirecionamento HTTP `302` esperado.
- API sem chave: HTTP `403` esperado.
- Suite local: 18 testes aprovados com SQLite isolado.
- Logs posteriores a recriacao: sem erros de aplicacao observados.
- Dump PostgreSQL posterior a migracao criado e protegido fora do repositorio.

## Configuracao de runtime

O Compose versionado publica o Nginx da aplicacao somente no loopback do host.
O backend nao publica sua porta diretamente e permanece acessivel apenas pelo
Nginx do mesmo projeto.

Os containers mantem as protecoes:

- filesystem somente leitura;
- `cap_drop: ALL`;
- `no-new-privileges`;
- volumes exclusivos para static e media;
- limites de rotacao dos logs Docker;
- politica `restart: unless-stopped`.

## Pendencias

### Exposicao externa

Na validacao final, o Nginx do host permanecia inativo e o Nginx do container
estava vinculado somente ao loopback. Assim, a aplicacao funcionava
internamente na VPS, mas nao estava publicada externamente.

Definir uma das arquiteturas antes de liberar trafego:

1. ativar o Nginx do host nas portas publicas e encaminhar para uma porta de
   loopback exclusiva do container; ou
2. revisar conscientemente o bind do container e as regras de firewall.

A primeira opcao preserva a arquitetura documentada e facilita a futura
terminacao TLS. Alteracoes no Nginx do host e no firewall exigem privilegios
administrativos e ficaram fora desta execucao.

### HTTPS

O deploy continua em HTTP. Permanecem esperados os alertas Django para:

- HSTS desabilitado;
- redirecionamento HTTPS desabilitado;
- cookie de sessao sem `Secure`;
- cookie CSRF sem `Secure`.

Nao habilitar HSTS ou cookies `Secure` antes de confirmar que o acesso HTTPS
funciona de ponta a ponta.

### Operacao e recuperacao

- Testar periodicamente a restauracao do dump PostgreSQL em ambiente isolado.
- Definir retencao para o SQLite original e os artefatos da migracao.
- Nao remover o SQLite original ate concluir o periodo de observacao.
- Confirmar novos inserts com IDs posteriores aos importados.
- Monitorar logs e datas na virada do dia no fuso da aplicacao.

## Comandos seguros de verificacao

Executar no diretorio do projeto na VPS:

```bash
docker compose config --quiet
docker compose ps
docker compose exec -T backend python manage.py showmigrations --plan
docker compose exec -T backend python manage.py check --deploy
docker compose logs --tail=100 backend nginx
curl --fail http://localhost/healthz/
```

Esses comandos nao devem imprimir o `.env`, variaveis de ambiente, strings de
conexao ou conteudo dos backups.

## Rollback

O rollback deve usar os artefatos privados criados antes da migracao.

- Antes de novas escritas relevantes, e possivel parar o Compose e restaurar
  o fluxo anterior com o SQLite preservado.
- Depois de novas escritas no PostgreSQL, retornar diretamente ao SQLite pode
  perder dados e nao deve ser feito sem uma migracao reversa planejada.
- Para falha no PostgreSQL, restaurar o dump validado em banco isolado antes
  de substituir o banco operacional.
- Nunca registrar comandos com senhas literais no shell, em logs ou neste
  repositorio.

## Referencias

- `README.md`
- `compose.yml`
- `docs/specs/MIGRACAO_POSTGRES.md`
- `deploy/nginx/backend_pomodoro_task.conf.example`
