# Handoff — Estratégia de PostgreSQL para desenvolvimento e produção

## Objetivo

Padronizar como os projetos Django devem utilizar PostgreSQL em desenvolvimento e produção, preservando isolamento, segurança e consistência entre ambientes.

A arquitetura definida permite:

* um container PostgreSQL central na VPS atendendo vários projetos;
* um container PostgreSQL central no ambiente de desenvolvimento atendendo vários projetos locais;
* projetos Django executados em container;
* projetos Django executados diretamente no host durante o desenvolvimento.

---

# 1. Arquitetura de produção na VPS

Na VPS existe um PostgreSQL centralizado em Docker.

Esse PostgreSQL deve atender vários projetos, por exemplo:

```text
postgres
├── projeto1
├── projeto2
├── projeto3
└── projetoN
```

Cada projeto deve possuir preferencialmente:

* banco próprio;
* usuário próprio;
* senha própria;
* migrations próprias;
* permissões restritas ao próprio banco.

O PostgreSQL da VPS não deve publicar a porta `5432` externamente.

A comunicação ocorre exclusivamente pela rede Docker externa:

```text
backend_net
```

Arquitetura:

```text
Container PostgreSQL
        |
        | backend_net
        |
├── Container projeto1
├── Container projeto2
├── Container projeto3
└── Container projetoN
```

---

# 2. Rede Docker externa

O Compose do PostgreSQL utiliza:

```yaml
networks:
  backend_net:
    external: true
```

Quando uma rede é declarada como externa, o Docker Compose não cria essa rede automaticamente.

Ela deve ser criada uma única vez em cada máquina:

```bash
docker network create backend_net
```

Validação:

```bash
docker network ls | grep backend_net
```

Comando idempotente:

```bash
docker network inspect backend_net >/dev/null 2>&1 \
  || docker network create backend_net
```

Depois disso:

```bash
docker compose up -d
```

---

# 3. Compose do PostgreSQL na VPS

Local atual:

```text
/home/alisson/postgres/compose.yml
```

Configuração:

```yaml
services:
  postgres:
    image: postgres:16
    container_name: postgres
    restart: unless-stopped
    environment:
      POSTGRES_DB: ${POSTGRES_DB}
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      TZ: ${TZ}
    volumes:
      - /opt/postgres/data:/var/lib/postgresql/data
      - /opt/postgres/backups:/backups
    networks:
      - backend_net
    command:
      - "postgres"
      - "-c"
      - "shared_buffers=128MB"
      - "-c"
      - "max_connections=40"
      - "-c"
      - "work_mem=4MB"
      - "-c"
      - "maintenance_work_mem=64MB"

  pgadmin:
    image: dpage/pgadmin4:latest
    container_name: pgadmin
    restart: unless-stopped
    depends_on:
      - postgres
    environment:
      PGADMIN_DEFAULT_EMAIL: ${PGADMIN_DEFAULT_EMAIL}
      PGADMIN_DEFAULT_PASSWORD: ${PGADMIN_DEFAULT_PASSWORD}
      TZ: ${TZ}
    volumes:
      - pgadmin_data:/var/lib/pgadmin
    ports:
      - "${TAILSCALE_IP}:${PGADMIN_PORT}:80"
    networks:
      - backend_net

volumes:
  pgadmin_data:

networks:
  backend_net:
    external: true
```

Regras:

* não adicionar publicação da porta `5432`;
* não publicar o PostgreSQL em `0.0.0.0`;
* não publicar o PostgreSQL no IP público;
* não publicar o PostgreSQL no IP Tailscale;
* o pgAdmin pode continuar publicado apenas no IP Tailscale;
* os backends acessam o banco pela rede `backend_net`.

---

# 4. Compose de produção de cada projeto Django

Cada projeto deve possuir seu próprio Compose.

Exemplo:

```yaml
services:
  backend:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: projeto1-backend
    restart: unless-stopped
    env_file:
      - .env
    networks:
      - backend_net

networks:
  backend_net:
    external: true
```

O Compose do projeto não deve criar outro PostgreSQL quando for utilizar o PostgreSQL central da VPS.

O projeto deve utilizar:

```env
DB_HOST=postgres
DB_PORT=5432
```

Não utilizar em produção:

```env
DB_HOST=localhost
DB_HOST=127.0.0.1
DB_HOST=100.85.87.18
```

Dentro do container Django, `localhost` representa o próprio container Django.

O nome `postgres` é resolvido pelo DNS interno do Docker, porque ambos estão conectados à rede `backend_net`.

---

# 5. Desenvolvimento local

No desenvolvimento, existem dois cenários válidos.

---

## Cenário A — Django e PostgreSQL em containers

Arquitetura:

```text
Container Django
        |
        | backend_net
        v
Container PostgreSQL
```

Nesse caso:

```env
DB_HOST=postgres
DB_PORT=5432
```

O PostgreSQL não precisa publicar a porta `5432`, pois a comunicação ocorre entre containers.

Exemplo do PostgreSQL local:

```yaml
services:
  postgres:
    image: postgres:16
    container_name: postgres
    environment:
      POSTGRES_DB: ${POSTGRES_DB}
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - postgres_dev_data:/var/lib/postgresql/data
    networks:
      - backend_net

volumes:
  postgres_dev_data:

networks:
  backend_net:
    external: true
```

Exemplo do projeto:

```yaml
services:
  backend:
    build:
      context: .
    env_file:
      - .env
    networks:
      - backend_net

networks:
  backend_net:
    external: true
```

No desenvolvimento, não utilizar automaticamente:

```yaml
restart: unless-stopped
```

Os containers locais devem ser iniciados e interrompidos manualmente conforme a necessidade.

---

## Cenário B — PostgreSQL em container e Django executado no host

Arquitetura:

```text
Django executado no Linux
            |
            | 127.0.0.1:5432
            v
Container PostgreSQL
```

Nesse cenário, o processo Django não participa diretamente da rede Docker.

Por isso, o PostgreSQL local precisa publicar a porta somente em localhost:

```yaml
ports:
  - "127.0.0.1:5432:5432"
```

Exemplo:

```yaml
services:
  postgres:
    image: postgres:16
    container_name: postgres
    environment:
      POSTGRES_DB: ${POSTGRES_DB}
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - postgres_dev_data:/var/lib/postgresql/data
    ports:
      - "127.0.0.1:5432:5432"
    networks:
      - backend_net

volumes:
  postgres_dev_data:

networks:
  backend_net:
    external: true
```

No `.env` do Django local:

```env
DB_HOST=127.0.0.1
DB_PORT=5432
```

Evitar:

```yaml
ports:
  - "5432:5432"
```

Essa forma pode publicar o PostgreSQL em todas as interfaces de rede da máquina.

---

# 6. PostgreSQL central também no desenvolvimento

O desenvolvimento pode utilizar um único container PostgreSQL para vários projetos locais.

Exemplo:

```text
postgres local
├── projeto1_dev
├── projeto2_dev
├── projeto3_dev
└── projetoN_dev
```

Cada projeto deve ter banco e usuário próprios.

Exemplo:

```sql
CREATE USER projeto1_dev_user
WITH PASSWORD 'senha_local_forte';

CREATE DATABASE projeto1_dev
WITH OWNER projeto1_dev_user;

CREATE USER projeto2_dev_user
WITH PASSWORD 'outra_senha_local';

CREATE DATABASE projeto2_dev
WITH OWNER projeto2_dev_user;
```

Não utilizar o mesmo banco para projetos diferentes.

Não utilizar o mesmo usuário administrativo para todas as aplicações.

---

# 7. Variáveis de ambiente

O Django deve utilizar:

```env
DB_NAME=
DB_USER=
DB_PASS=
DB_HOST=
DB_PORT=5432
```

Exemplo para desenvolvimento com Django em container:

```env
DB_NAME=projeto1_dev
DB_USER=projeto1_dev_user
DB_PASS=senha_local
DB_HOST=postgres
DB_PORT=5432
```

Exemplo para desenvolvimento com Django no host:

```env
DB_NAME=projeto1_dev
DB_USER=projeto1_dev_user
DB_PASS=senha_local
DB_HOST=127.0.0.1
DB_PORT=5432
```

Exemplo para produção:

```env
DB_NAME=projeto1
DB_USER=projeto1_user
DB_PASS=senha_forte
DB_HOST=postgres
DB_PORT=5432
```

---

# 8. Driver PostgreSQL

O projeto Django deve possuir um driver PostgreSQL compatível.

Preferência:

```text
psycopg
```

Com Poetry:

```bash
poetry add "psycopg[binary]"
```

Antes de instalar:

1. verificar dependências atuais;
2. localizar `pyproject.toml`;
3. verificar se já existe `psycopg`;
4. verificar se existe `psycopg2`;
5. evitar dependências duplicadas ou conflitantes.

---

# 9. Migrations do Django

O schema do banco não deve ser copiado manualmente do ambiente local para a VPS.

O fluxo correto é baseado nas migrations versionadas.

No desenvolvimento:

```bash
python manage.py makemigrations
python manage.py migrate
python manage.py test
```

Os arquivos de migration devem ser versionados:

```text
app/
└── migrations/
    ├── 0001_initial.py
    ├── 0002_*.py
    └── 0003_*.py
```

Não adicionar migrations ao `.gitignore`.

Na produção:

```bash
python manage.py migrate --noinput
```

Não executar normalmente na VPS:

```bash
python manage.py makemigrations
```

A VPS deve apenas aplicar migrations já criadas, revisadas e testadas localmente.

O controle das migrations aplicadas fica na tabela:

```text
django_migrations
```

---

# 10. Processo de deploy

Fluxo recomendado:

```bash
git pull
docker compose build
docker compose run --rm backend python manage.py migrate --noinput
docker compose up -d
```

Quando houver arquivos estáticos:

```bash
docker compose run --rm backend python manage.py collectstatic --noinput
```

É preferível aplicar migrations antes de iniciar definitivamente a nova versão.

Se a migration falhar, o deploy não deve ser considerado concluído.

---

# 11. Sobre POSTGRES_DB

A variável:

```yaml
POSTGRES_DB: ${POSTGRES_DB}
```

é utilizada principalmente na primeira inicialização do cluster PostgreSQL.

Quando o volume já está inicializado, alterar `POSTGRES_DB` no `.env` não cria automaticamente outro banco.

Na VPS, o cluster já está persistido em:

```text
/opt/postgres/data
```

Novos bancos e usuários devem ser criados explicitamente por SQL ou pelo pgAdmin.

---

# 12. Segurança

Regras obrigatórias:

1. Produção e desenvolvimento devem usar bancos separados.
2. O banco da VPS não deve ser utilizado para desenvolvimento.
3. O PostgreSQL da VPS não deve publicar a porta `5432`.
4. A comunicação em produção deve ocorrer pela rede `backend_net`.
5. O pgAdmin pode ser publicado apenas no IP Tailscale.
6. No desenvolvimento com Django no host, publicar PostgreSQL somente em `127.0.0.1`.
7. Não utilizar `0.0.0.0:5432`.
8. Não versionar arquivos `.env`.
9. Manter um `.env.example` sem credenciais reais.
10. Cada projeto deve possuir banco e usuário próprios.
11. Migrations devem ser criadas localmente.
12. A VPS deve apenas aplicar migrations versionadas.

---

# 13. Política de restart

Produção:

```yaml
restart: unless-stopped
```

Desenvolvimento:

```yaml
# não utilizar restart automático
```

Motivo:

* produção deve recuperar os serviços após reinicializações;
* desenvolvimento deve permitir controle manual dos containers;
* evita containers locais iniciando automaticamente sem necessidade.

---

# 14. Estrutura final

## Produção

```text
VPS
├── postgres
│   ├── container PostgreSQL
│   ├── /opt/postgres/data
│   ├── /opt/postgres/backups
│   └── backend_net
│
├── pgadmin
│   ├── backend_net
│   └── publicado somente no IP Tailscale
│
├── projeto1-backend
│   ├── backend_net
│   └── DB_HOST=postgres
│
├── projeto2-backend
│   ├── backend_net
│   └── DB_HOST=postgres
│
└── projetoN-backend
    ├── backend_net
    └── DB_HOST=postgres
```

## Desenvolvimento com projeto em container

```text
Docker local
├── postgres
│   └── backend_net
│
└── projeto-backend
    ├── backend_net
    └── DB_HOST=postgres
```

## Desenvolvimento com projeto fora do container

```text
Host Linux
├── Django
│   └── DB_HOST=127.0.0.1
│
└── Docker
    └── PostgreSQL
        └── 127.0.0.1:5432
```

---

# 15. Instruções para o Codex

Antes de implementar alterações:

1. Analise o estado atual do projeto.
2. Localize:

   * `compose.yml`;
   * `docker-compose.yml`;
   * `Dockerfile`;
   * `.env`;
   * `.env.example`;
   * `pyproject.toml`;
   * `requirements.txt`;
   * configuração de banco no Django;
   * scripts de inicialização;
   * documentação de deploy;
   * migrations existentes.
3. Identifique se o Django roda:

   * em container;
   * diretamente no host;
   * de ambas as formas.
4. Verifique se já existe integração com `backend_net`.
5. Verifique se a rede precisa ser criada.
6. Verifique se o driver PostgreSQL está instalado.
7. Não sobrescreva configurações válidas sem necessidade.
8. Não alterar o Compose central do PostgreSQL da VPS sem justificativa explícita.
9. Não publicar a porta `5432` na VPS.
10. Não executar `makemigrations` na VPS.
11. Não apagar volumes existentes.
12. Não recriar o cluster PostgreSQL.
13. Não remover o WSL do ambiente de desenvolvimento.
14. Não ler ou expor credenciais reais na resposta final.

---

# 16. Validações obrigatórias

Executar o que for aplicável:

```bash
docker network inspect backend_net
```

```bash
docker compose config
```

```bash
docker compose ps
```

```bash
python manage.py check
```

```bash
python manage.py makemigrations --check --dry-run
```

```bash
python manage.py migrate --plan
```

```bash
python manage.py test
```

Com Poetry:

```bash
poetry check
```

Validar também:

* a rede `backend_net` existe;
* o container PostgreSQL está conectado à rede;
* o backend de produção está conectado à rede;
* o backend resolve o host `postgres`;
* o desenvolvimento usa `127.0.0.1` quando o Django roda no host;
* o desenvolvimento usa `postgres` quando o Django roda em container;
* nenhuma credencial real está versionada;
* a porta `5432` não está publicada na VPS;
* a porta local está restrita a `127.0.0.1`;
* o Compose de desenvolvimento não possui restart automático;
* o Compose de produção mantém `restart: unless-stopped`.

---

# 17. Entrega esperada

Ao concluir:

1. Apresentar o estado encontrado.
2. Informar os arquivos alterados.
3. Explicar qual cenário de desenvolvimento foi adotado.
4. Informar como subir o PostgreSQL local.
5. Informar como criar a rede `backend_net`.
6. Informar como executar o projeto localmente.
7. Informar como aplicar migrations.
8. Informar como fazer o deploy na VPS.
9. Informar os resultados das validações.
10. Registrar riscos e pendências.
11. Atualizar a documentação correspondente.
12. Criar commit Git ao final.

Mensagem sugerida:

```text
chore: padroniza banco PostgreSQL entre desenvolvimento e produção
```

Não realizar `push` sem solicitação explícita.

---

# 18. Regra principal

A decisão final é:

```text
Produção:
container Django -> backend_net -> PostgreSQL central da VPS

Desenvolvimento em container:
container Django -> backend_net -> PostgreSQL central local

Desenvolvimento fora do container:
Django no host -> 127.0.0.1:5432 -> PostgreSQL central local
```

Os schemas chegam à produção exclusivamente por migrations Django versionadas no Git.
