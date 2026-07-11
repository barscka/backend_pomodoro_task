---

spec_id: SPEC-BACK-001
titulo: Migração do SQLite para PostgreSQL
status: APPROVED
fase: AS_IS
situacao: VIGENTE
responsavel: Arquitetura de Software
criado_em: 2026-06-21
atualizado_em: 2026-07-11
documento_principal:

* SPEC-BACK-001
  dependencias: []
  substitui: []
  substituida_por: []

---

# SPEC-BACK-001 — Migração do SQLite para PostgreSQL

## 1. Objetivo

Definir e documentar a migração do backend Django do banco SQLite legado para PostgreSQL, preservando os dados existentes, isolando os testes automatizados e estabelecendo uma arquitetura segura para desenvolvimento local e deploy em Docker.

Esta Specification também registra o estado efetivamente implementado da migração, diferenciando:

* o que já foi concluído no ambiente local;
* o que foi validado tecnicamente;
* o que permanece pendente de comprovação na VPS;
* os cuidados necessários para manutenção do banco legado e execução dos testes.

---

## 2. Estado da Specification

### Classificação geral

**PARCIALMENTE CONFIRMADO**

A implementação técnica da migração está concluída e validada no ambiente local.

Estão confirmados nos fontes e configurações atuais:

* suporte ao PostgreSQL;
* driver PostgreSQL adicionado;
* settings separados por ambiente;
* PostgreSQL como banco operacional em desenvolvimento e produção;
* SQLite restrito ao ambiente de testes e migração legada;
* Dockerfile da aplicação;
* Compose da aplicação;
* Gunicorn;
* Nginx;
* rede Docker externa para acesso ao PostgreSQL central;
* proteção contra uso acidental do banco real pelos testes;
* estratégia documentada de migração dos dados.

O deploy definitivo e o cutover completo na VPS não foram comprovados por evidência operacional anexada nesta revisão.

---

## 3. Contexto

O backend foi originalmente desenvolvido utilizando SQLite em:

```text
db.sqlite3
```

Com a evolução do projeto, passaram a existir requisitos que justificam o uso de PostgreSQL:

* execução em Docker;
* persistência centralizada;
* maior previsibilidade transacional;
* constraints condicionais;
* uso de `select_for_update`;
* controle concorrente de filas e execuções;
* preparação para operação contínua;
* necessidade de separar banco de desenvolvimento, teste e produção.

A arquitetura adotada reutiliza um PostgreSQL central, compartilhado entre diferentes projetos no nível de infraestrutura, mas com banco e usuário exclusivos para cada aplicação.

O projeto Pomodoro não cria nem administra um container PostgreSQL próprio em seu Compose.

---

## 4. Escopo

Esta Specification contempla:

* substituição do SQLite como banco operacional;
* configuração do PostgreSQL para desenvolvimento;
* configuração do PostgreSQL para produção;
* isolamento dos testes em SQLite;
* proteção contra conexão acidental dos testes ao banco real;
* preservação do SQLite legado;
* transferência dos dados existentes;
* containerização do backend;
* conexão do backend à rede Docker externa;
* configuração do Gunicorn;
* configuração do Nginx;
* validação das migrations;
* validação dos dados migrados;
* documentação de execução, deploy e rollback;
* preservação das API Keys existentes;
* preservação dos usuários Django;
* preservação dos dados dos domínios `pomodoro` e `user_profile`.

---

## 5. Fora de escopo

Não fazem parte desta Specification:

* alterar regras funcionais do Pomodoro;
* alterar a lógica da fila de atividades;
* alterar a randomização ponderada;
* alterar o histórico de atividades puladas;
* alterar o contrato HTTP dos endpoints;
* substituir PostgreSQL por outro banco;
* criar um PostgreSQL exclusivo dentro deste projeto;
* alterar o Compose do PostgreSQL central;
* expor a porta 5432 publicamente;
* atualizar Python, Django ou demais dependências sem relação com a migração;
* migrar automaticamente sessões descartáveis;
* migrar logs operacionais descartáveis;
* excluir definitivamente o SQLite legado;
* remover backups após o cutover;
* implementar TLS;
* reformular a infraestrutura global da VPS.

---

## 6. Arquitetura de banco adotada

### 6.1 Desenvolvimento local

O backend deve utilizar o PostgreSQL central local.

Quando a aplicação Django for executada diretamente no host:

```env
APP_ENV=development
DB_HOST=127.0.0.1
DB_PORT=5432
DB_NAME=pomodoro_task_dev
DB_USER=pomodoro_task_dev_user
DB_PASS=<senha_local>
```

Banco e usuário definidos para desenvolvimento:

```text
Banco: pomodoro_task_dev
Usuário: pomodoro_task_dev_user
```

O backend não cria nem administra o container PostgreSQL local.

### 6.2 Produção

O backend containerizado acessa o PostgreSQL central pela rede Docker externa:

```env
APP_ENV=production
DB_HOST=postgres
DB_PORT=5432
DB_NAME=pomodoro_task_prod
DB_USER=pomodoro_task_user
DB_PASS=<senha_de_producao>
```

Banco e usuário definidos para produção:

```text
Banco: pomodoro_task_prod
Usuário: pomodoro_task_user
```

A aplicação não deve utilizar o usuário administrativo do PostgreSQL.

### 6.3 Testes automatizados

Os testes continuam utilizando SQLite isolado:

```env
APP_ENV=test
TESTING=true
TEST_DATABASE_URL=sqlite:///tests/.tmp/app_test.sqlite
```

O banco de teste:

* não pode ser o `db.sqlite3` legado;
* não pode utilizar PostgreSQL de desenvolvimento;
* não pode utilizar PostgreSQL de produção;
* deve ser criado em diretório temporário;
* deve poder ser descartado após a suíte.

### 6.4 Migração legada

O SQLite original deve ser preservado.

A atualização das migrations e a extração dos dados devem ocorrer em uma cópia temporária, utilizando settings próprios para o banco legado.

Arquivo relacionado:

```text
config/settings/legacy_sqlite.py
```

---

## 7. Estrutura de settings

A configuração atual encontra-se separada por ambiente:

```text
config/settings/base.py
config/settings/database.py
config/settings/local.py
config/settings/production.py
config/settings/test.py
config/settings/legacy_sqlite.py
```

### 7.1 `base.py`

Responsável pelas configurações compartilhadas da aplicação.

Deve manter:

```python
TIME_ZONE = "America/Sao_Paulo"
USE_TZ = True
```

Não deve assumir silenciosamente um banco operacional inseguro.

### 7.2 `database.py`

Responsável por centralizar a construção e validação da configuração de banco.

Deve impedir:

* produção sem variáveis obrigatórias;
* testes usando PostgreSQL;
* testes usando o SQLite legado;
* uso de caminho de teste fora do diretório temporário autorizado.

### 7.3 `local.py`

Responsável pelo ambiente de desenvolvimento.

O banco operacional deve ser PostgreSQL.

### 7.4 `production.py`

Responsável pelo ambiente de produção.

A inicialização deve falhar quando variáveis obrigatórias estiverem ausentes.

### 7.5 `test.py`

Responsável pelo isolamento da suíte automatizada.

Não deve aceitar fallback para banco real.

### 7.6 `legacy_sqlite.py`

Responsável exclusivamente por:

* leitura da cópia do SQLite legado;
* aplicação de migrations sobre a cópia;
* geração de fixture;
* validação dos dados de origem.

Não deve ser utilizado como settings operacional da aplicação.

---

## 8. Dados de origem

O SQLite analisado antes da migração possuía:

* integridade válida;
* ausência de FKs órfãs;
* dados de domínio;
* usuário Django;
* API Key;
* migrations pendentes na cópia legada.

Contagens registradas antes da migração:

| Tabela lógica              | Registros |
| -------------------------- | --------: |
| Usuários Django            |         1 |
| API Keys                   |         1 |
| Atividades                 |        22 |
| Categorias                 |         6 |
| Agendamentos               |         7 |
| Históricos                 |         7 |
| Perfis                     |         1 |
| Hard skills                |         6 |
| Soft skills                |         5 |
| Idiomas                    |         2 |
| Itens de portfólio         |         3 |
| Experiências profissionais |         3 |

Essas contagens constituem o baseline documental da migração original.

Caso uma nova migração seja executada, devem ser coletadas novas contagens antes do cutover.

---

## 9. Dados transferidos

Devem ser transferidos:

* modelos do domínio `pomodoro`;
* modelos do domínio `user_profile`;
* usuários Django necessários;
* API Keys com seus hashes;
* relacionamentos entre atividades, categorias, grupos, agendamentos e históricos;
* dados funcionais necessários para continuidade da aplicação.

Não devem ser transferidos por padrão:

* tabela de histórico de migrations;
* sessões ativas;
* logs do Django Admin;
* permissões e content types gerados automaticamente;
* artefatos temporários;
* logs operacionais descartáveis.

Os seguintes dados devem ser recriados pelas migrations:

* `django_content_type`;
* permissões;
* schema do banco;
* índices;
* constraints;
* sequences.

---

## 10. Estratégia de transferência

A transferência entre SQLite e PostgreSQL deve utilizar os mecanismos de serialização do Django.

Fluxo adotado:

1. preservar o `db.sqlite3` original;
2. criar uma cópia temporária;
3. registrar o checksum SHA-256;
4. executar verificações de integridade;
5. aplicar migrations pendentes somente na cópia;
6. exportar os modelos definidos;
7. criar um PostgreSQL vazio;
8. executar todas as migrations no PostgreSQL;
9. importar a fixture;
10. ajustar as sequences;
11. comparar dados de origem e destino;
12. validar os endpoints;
13. validar a API Key;
14. validar novos inserts.

Não deve ser feita cópia direta de arquivo, tabela ou schema entre engines.

---

## 11. Infraestrutura Docker

### 11.1 Imagem do backend

O projeto possui Dockerfile multi-stage com execução por usuário não privilegiado.

A imagem deve:

* instalar somente dependências necessárias no runtime;
* não incluir `.env`;
* não incluir o SQLite legado;
* não incluir o ambiente virtual local;
* não executar como `root`;
* inicializar o Gunicorn com settings de produção.

### 11.2 Compose da aplicação

O Compose da aplicação:

* não cria PostgreSQL;
* conecta o backend à rede externa `backend_net`;
* mantém uma rede interna para comunicação entre Nginx e Gunicorn;
* não publica a porta do Gunicorn diretamente;
* publica o Nginx somente em loopback;
* utiliza volumes separados para static e media;
* possui health checks;
* utiliza filesystem somente leitura quando aplicável;
* remove capabilities desnecessárias;
* ativa `no-new-privileges`.

### 11.3 PostgreSQL central

O PostgreSQL é administrado fora deste projeto.

O backend deve conectar utilizando:

```text
postgres:5432
```

quando estiver na rede Docker.

A porta 5432 não deve ser publicada publicamente.

---

## 12. Migrações

A troca de engine não exige migrations de modelo apenas por substituir SQLite por PostgreSQL.

As migrations existentes devem ser suficientes para criar o schema no PostgreSQL.

Devem ser executados:

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
python manage.py migrate --noinput
```

O deploy não deve permitir múltiplos workers executando migrations simultaneamente.

As migrations devem ser executadas como etapa explícita de release.

---

## 13. Testes

### 13.1 Suíte padrão

A suíte deve ser executada utilizando:

```bash
APP_ENV=test poetry run python manage.py test --settings=config.settings.test
```

Ou comando equivalente definido no projeto.

### 13.2 Proteção do banco

Os testes devem validar que:

* o engine utilizado é SQLite;
* o caminho pertence a `tests/.tmp`;
* o arquivo não é o SQLite legado;
* `DB_HOST` de desenvolvimento não é utilizado;
* `DB_HOST` de produção não é utilizado;
* `DATABASE_URL` externa não é usada como fallback.

### 13.3 Limitação conhecida

A suíte em SQLite não reproduz integralmente comportamentos específicos do PostgreSQL, especialmente:

* constraints parciais;
* locks;
* `select_for_update`;
* concorrência;
* coerção de data e horário;
* tipos temporais;
* comportamento de sequences.

Funcionalidades dependentes desses recursos devem possuir validação adicional em PostgreSQL.

A correção descrita na `SPEC-BACK-006` é um exemplo de incompatibilidade não detectada somente pelo SQLite.

---

## 14. Validações de dados

Após a importação, devem ser validados:

* contagem por modelo;
* maior PK por tabela;
* ausência de FK órfã;
* ausência de duplicidades indevidas;
* unicidade dos registros;
* grupo padrão;
* vínculos entre categorias e grupos;
* agendamentos;
* históricos;
* usuários;
* API Keys;
* valores de data e horário;
* sequences;
* novos inserts.

A validação deve confirmar que os próximos IDs gerados são maiores que os IDs importados.

---

## 15. Validações funcionais

Após a migração, devem ser exercitados:

* health check;
* autenticação por API Key;
* listagem de grupos;
* listagem de atividades;
* obtenção da próxima atividade;
* criação ou leitura da fila;
* início de atividade;
* consulta da atividade ativa;
* conclusão;
* histórico;
* ação de pular;
* persistência do histórico de atividades puladas;
* seleção das próximas atividades.

A existência de resposta HTTP não é suficiente.

Deve ser validada a persistência efetiva no PostgreSQL.

---

## 16. Timezone

O projeto utiliza:

```text
America/Sao_Paulo
```

e:

```python
USE_TZ = True
```

Campos `DateTimeField` devem armazenar instantes timezone-aware.

Campos `DateField` e `TimeField` devem receber valores compatíveis com seus tipos.

Deve haver cuidado especial ao converter:

```python
timezone.now()
```

para:

* data local;
* horário local;
* campos sem timezone.

Não deve ser utilizado diretamente:

```python
timezone.now().time()
```

em `TimeField` sem garantir que o valor esteja convertido para horário local e sem `tzinfo`.

A correção específica desse comportamento está registrada na:

```text
SPEC-BACK-006
```

---

## 17. Segurança

Devem ser mantidas as seguintes regras:

* banco e usuário exclusivos para a aplicação;
* senha armazenada somente em `.env` não versionado;
* `.env` com permissões restritas;
* nenhuma credencial em Dockerfile;
* nenhuma credencial no Compose versionado;
* nenhuma credencial em logs;
* nenhuma credencial na documentação;
* PostgreSQL sem exposição pública;
* backend executado por usuário não privilegiado;
* Gunicorn inacessível diretamente pelo host;
* acesso externo somente por Nginx;
* filesystem somente leitura quando possível;
* capabilities removidas;
* API Keys preservadas somente como hashes.

---

## 18. Deploy

### 18.1 Preparação

Antes do deploy:

1. confirmar saúde do PostgreSQL central;
2. confirmar a rede `backend_net`;
3. criar banco e usuário;
4. configurar o `.env`;
5. realizar backup;
6. executar migrations;
7. validar conectividade;
8. validar health check;
9. preservar o SQLite original.

### 18.2 Cutover

O cutover deve seguir:

1. interromper escritas no backend antigo;
2. parar o processo antigo;
3. copiar o SQLite final;
4. registrar checksum;
5. atualizar a cópia com migrations;
6. gerar fixture final;
7. migrar o PostgreSQL;
8. importar os dados;
9. ajustar sequences;
10. validar contagens;
11. subir os containers;
12. validar endpoints;
13. liberar o tráfego.

### 18.3 Estado atual

**PARCIALMENTE CONFIRMADO**

Está confirmado:

* funcionamento local com PostgreSQL;
* schema criado pelas migrations;
* importação de dados em ambiente de ensaio;
* imagem Docker criada;
* Compose criado;
* Gunicorn validado;
* Nginx validado;
* comunicação por rede Docker;
* execução não privilegiada;
* health check;
* arquivos estáticos;
* operação temporária em HTTP.

Não está comprovado nesta revisão:

* cutover final na VPS;
* importação final do banco legado na VPS;
* comparação final das contagens em produção;
* período de observação pós-cutover;
* teste formal de rollback na VPS.

---

## 19. Rollback

### 19.1 Antes da liberação de novas escritas

É permitido:

1. parar os containers novos;
2. preservar o PostgreSQL para diagnóstico;
3. restaurar o processo anterior;
4. reapontar para o SQLite original;
5. validar autenticação e endpoints;
6. reabrir o tráfego.

### 19.2 Depois da liberação de novas escritas

O retorno direto ao SQLite pode causar perda de dados.

Nesse estágio, o procedimento recomendado é:

* corrigir progressivamente o PostgreSQL;
* preservar os dados escritos;
* não sobrescrever o banco;
* elaborar plano específico para transporte reverso, caso estritamente necessário.

Não fazem parte do rollback automático:

* excluir banco;
* excluir usuário;
* excluir schema;
* excluir volume;
* excluir backup;
* sobrescrever o SQLite legado.

---

## 20. Riscos e mitigação

| Risco                                   | Mitigação                                          |
| --------------------------------------- | -------------------------------------------------- |
| Perda de dados no SQLite                | Trabalhar em cópia e preservar o original.         |
| Migrations pendentes alterarem a origem | Aplicar migrations somente na cópia temporária.    |
| Diferenças entre SQLite e PostgreSQL    | Validar o fluxo real no PostgreSQL.                |
| Testes atingirem banco real             | Settings exclusivos e validações explícitas.       |
| Sequences ficarem abaixo das PKs        | Ajustar e validar sequences após a carga.          |
| API Key deixar de funcionar             | Migrar hashes e realizar teste autenticado.        |
| Timezone alterar datas ou horários      | Validar data local, `DateTimeField` e `TimeField`. |
| Migrations concorrentes                 | Executar migration como etapa única de release.    |
| Aplicação usar usuário administrativo   | Criar usuário dedicado.                            |
| Porta PostgreSQL ser exposta            | Manter acesso somente pela rede Docker privada.    |
| Rollback perder novas escritas          | Manter janela sem escrita até o aceite.            |
| SQLite mascarar falhas do PostgreSQL    | Criar validações de integração em PostgreSQL.      |

---

## 21. Critérios de aceite

A migração será considerada aceita quando:

1. PostgreSQL for o banco operacional;
2. todas as migrations executarem em banco vazio;
3. a aplicação iniciar sem fallback para SQLite;
4. a suíte de testes utilizar SQLite isolado;
5. a suíte não acessar banco real;
6. os dados forem importados;
7. as contagens forem conferidas;
8. as sequences forem ajustadas;
9. usuários Django continuarem válidos;
10. API Keys continuarem válidas;
11. endpoints críticos funcionarem;
12. início e conclusão forem validados em PostgreSQL;
13. o container resolver `postgres`;
14. o backend acessar `postgres:5432`;
15. a porta 5432 não estiver pública;
16. segredos não estiverem versionados;
17. backup estiver disponível;
18. rollback estiver documentado;
19. o deploy reiniciar sem perda de dados;
20. o estado final estiver registrado na documentação.

---

## 22. Definition of Done

A Specification somente poderá ser considerada totalmente concluída quando:

* implementação local estiver validada;
* cutover na VPS estiver concluído;
* contagens finais estiverem registradas;
* API Key estiver validada na VPS;
* endpoints críticos estiverem validados;
* início e conclusão estiverem testados no PostgreSQL da VPS;
* sequences estiverem verificadas;
* backup estiver confirmado;
* rollback estiver testado ou formalmente validado;
* período de observação estiver concluído;
* documentação operacional estiver atualizada.

Enquanto não houver essas evidências da VPS, a Specification permanece:

```text
status: APPROVED
fase: AS_IS
situacao: VIGENTE
```

com implementação local confirmada e implantação final parcialmente confirmada.

---

## 23. Pendências

Permanecem pendentes de comprovação:

* confirmar o cutover definitivo na VPS;
* registrar data do cutover;
* registrar contagens finais de produção;
* confirmar os nomes definitivos do banco e usuário da VPS;
* confirmar retenção do SQLite legado;
* validar início e conclusão no PostgreSQL após a correção da `SPEC-BACK-006`;
* confirmar procedimento de backup;
* confirmar procedimento de restauração;
* registrar período de observação;
* confirmar se Nginx permanece no host ou em container no ambiente definitivo.

---

## 24. Documentação relacionada

| Documento                          | Relação                                                             |
| ---------------------------------- | ------------------------------------------------------------------- |
| `SPEC-BACK-002`                    | Ajuste do contrato de início e categoria padrão.                    |
| `SPEC-BACK-003`                    | Fila persistida e randomização ponderada.                           |
| `SPEC-BACK-004`                    | Atividade ativa persistente.                                        |
| `SPEC-BACK-005`                    | Contador e sincronização do frontend.                               |
| `SPEC-BACK-006`                    | Correção de data e horário no início e conclusão usando PostgreSQL. |
| `README.md`                        | Procedimentos de instalação, execução e deploy.                     |
| `config/settings/database.py`      | Construção e validação da configuração do banco.                    |
| `config/settings/legacy_sqlite.py` | Acesso controlado à cópia SQLite legada.                            |
| `compose.yml`                      | Execução containerizada do backend.                                 |
| `Dockerfile`                       | Imagem da aplicação.                                                |

---

## 25. Evidências confirmadas

Foram identificadas no estado atual do projeto:

* driver PostgreSQL configurado;
* settings separados;
* PostgreSQL usado operacionalmente;
* SQLite de testes isolado;
* proteção contra banco real nos testes;
* configuração legada separada;
* Dockerfile multi-stage;
* runtime não-root;
* Compose sem PostgreSQL próprio;
* rede externa `backend_net`;
* redes internas separadas;
* health checks;
* Gunicorn;
* Nginx;
* volumes para static e media;
* filesystem somente leitura;
* capabilities removidas;
* documentação de migração e rollback;
* ensaio de importação para PostgreSQL;
* preservação do SQLite original.

---

## 26. Divergências corrigidas nesta revisão

A versão anterior apresentava informações que correspondiam ao estado inicial do planejamento, mas que já não representavam os fontes atuais.

Foram atualizadas as seguintes afirmações:

| Informação anterior                              | Estado atualizado                                                               |
| ------------------------------------------------ | ------------------------------------------------------------------------------- |
| SQLite configurado como banco padrão operacional | PostgreSQL é o banco operacional; SQLite permanece em testes e migração legada. |
| Driver PostgreSQL inexistente                    | Driver PostgreSQL já foi adicionado.                                            |
| Dockerfile inexistente                           | Dockerfile multi-stage implementado.                                            |
| Compose inexistente                              | Compose implementado.                                                           |
| Settings centralizados apenas em `base.py`       | Settings separados por ambiente e configuração de banco centralizada.           |
| Testes sem isolamento confiável                  | Testes isolados em SQLite com proteções contra banco real.                      |
| Descoberta padrão quebrada                       | Evidências documentais indicam que a descoberta foi corrigida.                  |
| Containerização pendente                         | Containerização local validada.                                                 |
| Migração apenas planejada                        | Migração local e ensaio de dados executados.                                    |
| Deploy integralmente pendente                    | Infraestrutura de deploy implementada; cutover final da VPS não comprovado.     |

---

## 27. Histórico

| Data       | Versão | Alteração                                                                                                                                                                                                                                                             | Responsável             |
| ---------- | -----: | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------- |
| 2026-06-21 |    1.0 | Criação do plano de migração do SQLite para PostgreSQL, incluindo desenvolvimento, testes, containerização, transferência de dados, deploy e rollback.                                                                                                                | Arquitetura de Software |
| 2026-06-21 |    1.1 | Registro das evidências da implementação local, criação do PostgreSQL de desenvolvimento, settings separados, ensaio de importação e infraestrutura Docker.                                                                                                           | Arquitetura de Software |
| 2026-07-11 |    2.0 | Inclusão do identificador canônico `SPEC-BACK-001`, conversão da Specification de plano inicial para documento AS-IS, atualização do estado real dos fontes, correção de informações desatualizadas e inclusão da relação com a correção temporal da `SPEC-BACK-006`. | Arquitetura de Software |

---

## 28. Conclusão

A migração do SQLite para PostgreSQL está tecnicamente consolidada no código e no ambiente local.

O backend atualmente possui:

* suporte operacional ao PostgreSQL;
* separação segura de ambientes;
* testes isolados;
* estratégia segura para o SQLite legado;
* infraestrutura Docker;
* comunicação com PostgreSQL central;
* mecanismos de hardening;
* documentação de migração e rollback.

A Specification permanece vigente porque continua sendo a referência canônica da arquitetura de banco.

O encerramento integral depende da comprovação do cutover, das validações finais e do período de observação na VPS.
