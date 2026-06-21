# AGENTS.md base para projetos consumidores

Este projeto usa minhas skills pessoais do repositorio `skills_pessoais`.

## Ordem de leitura

1. Ler este `AGENTS.md`.
2. Ler `.personal-skills.json`.
3. Localizar o repositorio `skills_pessoais` por `.personal-skills.local.json`, variavel `PERSONAL_SKILLS_HOME` ou caminhos comuns no Linux.
4. Carregar apenas os arquivos listados em `enabled_stacks`, `default_profile`, `profile_required_files` e `required_files`.

## Regras

- Nao copiar as pastas `personal-*` para dentro deste projeto.
- Nao carregar o repositorio de skills inteiro por padrao.
- Preferir os padroes locais do projeto quando forem saudaveis.
- Preservar trabalho existente e nao reverter mudancas alheias sem pedido explicito.
- Ao encerrar uma task com alteracoes, gerar commit coeso com mensagem em portugues do Brasil, salvo pedido explicito em contrario.
- Antes de fechar, informar padroes carregados, arquivos alterados, validacoes e riscos.

## Comandos uteis

```bash
python3 /caminho/skills_pessoais/tools/standards/doctor.py --project .
python3 /caminho/skills_pessoais/tools/standards/init_project.py --project .
```
