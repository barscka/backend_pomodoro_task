# Ajuste do contrato de início e da categoria padrão

## Status

- Tipo: spec/plano.
- Estado: pronto para implementação.
- Escopo: API Django/DRF, modelo de categorias, migração de dados e testes de contrato.
- Risco: médio, com alteração de chaves primárias e relacionamentos existentes.

## Objetivo

Corrigir o contrato de início de atividades para nunca responder `304 Not Modified` a uma requisição `POST` e estabelecer a categoria `Todos`, com ID fixo `1`, como categoria padrão de todas as atividades.

## Estado atual

### Início de atividade

- `POST /api/activities/<id>/start/` cria um `Schedule` e um `History` e responde `201 Created`.
- Quando já existe um agendamento aberto para a atividade no dia atual, o endpoint responde `304 Not Modified` com `schedule_id` no corpo.
- `304` é um status de validação de cache para requisições condicionais `GET` ou `HEAD`; não representa corretamente a idempotência de um `POST`.
- Clientes e proxies podem descartar o corpo de uma resposta `304`, impedindo a recuperação do `schedule_id` existente.

### Categoria de atividade

- `Activity.category` aceita `NULL`, usa `SET_NULL` na exclusão e não possui categoria padrão.
- `Activity.can_execute()` rejeita atividades sem categoria, mas `GET /api/activities/next/` não exclui explicitamente esses registros antes da seleção.
- O banco SQLite legado possui a categoria `Estudo` no ID `1`, associada a atividades. Portanto, o ID não pode ser sobrescrito sem realocação e atualização das FKs.
- A categoria `Todos` ainda não é garantida pelo schema nem pelas migrations atuais.
- Existe um grupo padrão também chamado `Todos`; grupo e categoria são entidades distintas e devem permanecer assim.

## Decisões de contrato

### `POST /api/activities/<id>/start/`

O endpoint deve ser idempotente para uma atividade que já possua agendamento aberto no dia atual:

- responder `201 Created` quando criar `Schedule` e `History`;
- responder `200 OK` quando reutilizar um `Schedule` aberto;
- retornar `schedule_id` nos dois casos;
- não criar um segundo `History` na reutilização;
- não retornar `304` em nenhuma resposta `POST`.

Resposta de criação:

```json
{
  "schedule_id": 10,
  "activity_id": 4,
  "date": "2026-06-22",
  "start_time": "14:30:00",
  "status": "started"
}
```

Resposta de reutilização:

```json
{
  "schedule_id": 10,
  "activity_id": 4,
  "date": "2026-06-22",
  "start_time": "14:30:00",
  "status": "already_started"
}
```

Os campos e valores de `status` devem ser estáveis e documentados. O frontend atual já aceita `200` e `201`, portanto a correção principal ocorre no backend.

### Categoria padrão

As seguintes invariantes devem valer após a migração:

- existe exatamente uma categoria chamada `Todos`;
- essa categoria possui obrigatoriamente `id = 1`;
- `Activity.category` é obrigatória e usa `default=1`;
- atividades atualmente sem categoria são vinculadas à categoria `Todos`;
- a categoria padrão não pode ser removida enquanto sustentar a invariável do modelo;
- novas atividades criadas sem `category` recebem a categoria de ID `1`;
- a categoria pertence ao grupo padrão vigente, sem assumir que o grupo padrão também possua ID `1`.

Valores iniciais propostos para a categoria padrão:

| Campo | Valor |
| --- | --- |
| `id` | `1` |
| `name` | `Todos` |
| `description` | `Categoria padrão para atividades sem classificação específica.` |
| `color` | `#FFFFFF` |
| `max_daily_executions` | valor definido explicitamente pela regra de produto antes da implementação |
| `executions_today` | `0` |
| `group` | grupo com `is_default=True` |

O limite diário da categoria `Todos` é uma decisão pendente. A implementação não deve escolher silenciosamente um valor que possa bloquear atividades migradas.

## Migração de dados

A mudança deve ser feita por migration Django transacional e validada primeiro no banco isolado de testes e em uma cópia do banco legado.

### Preparação

1. Identificar a categoria atual de ID `1` e todas as atividades relacionadas.
2. Identificar se já existe uma categoria chamada `Todos`, com comparação coerente com a unicidade atual do banco.
3. Resolver o grupo padrão por `is_default=True`; falhar de forma explícita se ele não existir ou se houver estado ambíguo.
4. Calcular um novo ID livre para realocar a categoria que atualmente ocupa o ID `1`.

### Realocação da categoria atual de ID `1`

Quando o ID `1` pertencer a outra categoria, como `Estudo`:

1. criar uma cópia integral dessa categoria em um novo ID livre;
2. atualizar `Activity.category_id` de `1` para o novo ID;
3. confirmar que nenhuma atividade continua ligada à categoria antiga;
4. remover o registro antigo de ID `1`;
5. criar `Todos` com ID `1`.

Não alterar a PK diretamente contando com `ON UPDATE CASCADE`, pois essa garantia não está declarada no modelo Django e pode variar entre SQLite e PostgreSQL.

### Categoria `Todos` preexistente

Se `Todos` já existir com outro ID:

1. vincular à categoria de ID `1` todas as atividades associadas ao registro preexistente;
2. preservar os valores de negócio definidos para `Todos` conforme decisão explícita da implementação;
3. remover o registro duplicado somente após verificar as FKs;
4. confirmar a unicidade do nome.

### Atividades sem categoria

Após criar a categoria padrão:

1. atualizar todas as atividades com `category_id IS NULL` para `category_id = 1`;
2. validar que não restam valores nulos;
3. alterar o campo para `null=False`, `blank=False` e `default=1`;
4. substituir `SET_NULL` por uma política compatível com a obrigatoriedade, preferencialmente `PROTECT`.

### Sequences

Depois de inserir ou realocar IDs explicitamente:

- ajustar a sequence de `Category` no PostgreSQL;
- validar que o próximo insert gera um ID livre maior que o maior ID existente;
- validar o mesmo comportamento no SQLite isolado de testes.

### Reversão

A migração de dados não deve prometer reversão automática da identidade anterior do ID `1`. O rollback operacional deve usar backup validado. Se houver `reverse_code`, ele deve apenas reverter alterações comprovadamente seguras e nunca reconstruir relacionamentos por suposição.

## Organização prevista

| Arquivo | Alteração planejada |
| --- | --- |
| `apps/pomodoro/models.py` | Tornar `Activity.category` obrigatória, definir `default=1` e proteger a categoria referenciada. |
| `apps/pomodoro/views.py` | Trocar o retorno de reutilização de `304` para `200` e estabilizar o payload de início. |
| `apps/pomodoro/migrations/0011_*.py` | Realocar a categoria atual de ID `1`, consolidar `Todos`, migrar nulos, alterar o campo e ajustar dados. |
| `apps/pomodoro/tests.py` ou `tests/api/` | Cobrir contrato HTTP, idempotência e invariantes da categoria padrão. |
| `README.md` | Documentar respostas `201` e `200` do endpoint, caso o README permaneça como referência pública da API. |

Se a lógica de resolução e realocação crescer, ela deve ser mantida legível e isolada. A migration usa modelos históricos por `apps.get_model()` e não importa os models atuais da aplicação.

## Plano de implementação

1. Definir o valor de `max_daily_executions` da categoria `Todos`.
2. Adicionar testes que reproduzam o `304`, a ausência de categoria padrão e o conflito atual no ID `1`.
3. Criar a migration de dados com cenários determinísticos para ID ocupado, `Todos` preexistente e categorias nulas.
4. Alterar `Activity.category` somente depois da normalização dos dados.
5. Alterar o endpoint `start` para retornar `200 OK` ao reutilizar o agendamento.
6. Padronizar o payload das respostas de criação e reutilização.
7. Executar a suíte no SQLite de testes isolado.
8. ensaiar a migration em uma cópia do SQLite legado.
9. Executar a migration em PostgreSQL descartável ou ambiente controlado e validar a sequence.
10. Revisar o contrato com o frontend sem expor API keys ou depender da API de produção.

## Testes obrigatórios

### Contrato de início

- primeira chamada retorna `201`, cria um `Schedule` e um `History`;
- segunda chamada para a mesma atividade e dia retorna `200`;
- as duas respostas retornam o mesmo `schedule_id`;
- a segunda chamada não cria registros adicionais;
- o payload de reutilização contém `activity_id`, `date`, `start_time` e `status`;
- limite diário e atividade inativa continuam retornando erro adequado;
- nenhum teste aceita `304` para `POST`.

### Categoria padrão

- banco vazio cria `Todos` com ID `1`;
- ID `1` ocupado é realocado sem perda de campos ou atividades;
- `Todos` em outro ID é consolidada sem duplicidade;
- atividades com categoria nula passam a apontar para ID `1`;
- criação de atividade sem categoria persiste `category_id = 1`;
- exclusão da categoria de ID `1` é rejeitada;
- `GET /api/activities/` e `GET /api/activities/next/` nunca serializam `category: null`;
- próxima inserção de categoria não colide com IDs existentes;
- execução repetida da função de migração, quando testada diretamente, não corrompe nem duplica dados.

## Validações

Executar apenas com o banco de testes isolado:

```bash
APP_ENV=test .venv/bin/python manage.py check --settings=config.settings.test
APP_ENV=test .venv/bin/python manage.py makemigrations --check --dry-run --settings=config.settings.test
APP_ENV=test .venv/bin/python manage.py migrate --plan --settings=config.settings.test
APP_ENV=test .venv/bin/python manage.py test --settings=config.settings.test
```

No ensaio de migração, comparar antes e depois:

- quantidade total de categorias;
- campos da categoria anteriormente localizada no ID `1`;
- quantidade de atividades por categoria;
- inexistência de `category_id IS NULL`;
- existência única de `Todos` no ID `1`;
- integridade de FKs;
- valor atual e próximo valor da sequence.

## Critérios de aceite

- nenhum `POST /api/activities/<id>/start/` retorna `304`;
- repetição do início retorna `200` e o mesmo `schedule_id` sem duplicar dados;
- existe uma única categoria `Todos` com ID `1`;
- nenhuma atividade possui categoria nula;
- novas atividades sem categoria explícita usam ID `1`;
- a categoria que ocupava o ID `1` preserva seus dados e relacionamentos em outro ID;
- migrations executam do zero e sobre uma cópia representativa do banco legado;
- sequences permanecem consistentes no PostgreSQL;
- suíte completa passa usando exclusivamente o banco isolado de testes.

## Riscos e mitigação

| Risco | Mitigação |
| --- | --- |
| Perda das atividades ligadas à categoria atual de ID `1` | Copiar a categoria, atualizar FKs e conferir contagens antes de remover o registro antigo. |
| Duplicidade do nome `Todos` | Consolidar registro preexistente durante a migration antes de criar a categoria de ID `1`. |
| Colisão da sequence após IDs explícitos | Resetar e validar a sequence no PostgreSQL após a migration. |
| Bloqueio indevido pelo limite diário de `Todos` | Definir `max_daily_executions` antes da implementação e cobrir o comportamento com testes. |
| Migration divergente entre SQLite e PostgreSQL | Evitar SQL específico quando possível e ensaiar nos dois engines. |
| Corpo descartado em respostas `304` | Usar `200 OK` com payload completo para o agendamento já existente. |
| Exclusão da categoria padrão quebrar a invariável | Usar política de exclusão protegida e teste explícito. |

## Fora de escopo

- alterar a identidade ou o ID do grupo padrão `Todos`;
- corrigir CORS, TLS ou configuração de transporte dos clientes;
- remodelar schedules para permitir mais de uma execução da mesma atividade no mesmo dia;
- implementar as mudanças descritas nesta spec;
- executar migrations em banco de desenvolvimento, compartilhado ou produção.
