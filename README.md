Aqui está o seu markdown ajustado e melhor formatado para o GitHub:

```markdown
# Pomodoro Personalizado - Backend

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue)](https://python.org)
[![Django](https://img.shields.io/badge/Django-4.2-green)](https://www.djangoproject.com)
[![Django REST Framework](https://img.shields.io/badge/DRF-3.14-red)](https://www.django-rest-framework.org)

Backend para o aplicativo Pomodoro Personalizado que gerencia atividades, agendamentos, histórico e dados do usuário.

## 📌 Funcionalidades Principais

- **Agendamento inteligente** de atividades
- **Ciclos Pomodoro** personalizáveis (1h trabalho / 5min descanso)
- **Limites diários**:
  - Máximo de 6 atividades por dia
  - Máximo de 2 atividades da mesma categoria por dia
- **Rotina automática** (segunda a sábado)
- **Histórico completo** com visualização mensal
- **Categorização** de atividades
- **Controle de pausas** (pausar e retomar atividades)

## 🛠 Tecnologias Utilizadas

- Python 3.12.4
- Django 5.2.1
- Django Rest Framework 3.16.0
- SQLite
- Poetry (Gerenciamento de dependências)

## 🚀 Configuração do Ambiente

1. **Clonar o repositório**:
   ```bash
   mkdir pomodoro_task
   cd pomodoro_task
   git clone git@github.com:barscka/backend_pomodoro_task.git
   cd backend_pomodoro_task
   ```

2. **Configurar ambiente virtual** (recomendado):
   ```bash
   python -m venv venv
   source venv/bin/activate  # Linux/Mac
   # ou
   venv\Scripts\activate  # Windows
   ```

3. **Instalar dependências**:
   ```bash
   poetry install
   ```

4. **Aplicar migrações**:
   ```bash
   python manage.py migrate
   ```

5. **Iniciar servidor de desenvolvimento**:
   ```bash
   python manage.py runserver
   ```

## 📊 Estrutura do Projeto

```
backend_pomodoro_task/
├── core/              # Aplicação principal
├── pomodoro/          # Lógica de agendamento Pomodoro
├── tasks/             # Gerenciamento de tarefas
├── users/             # Autenticação e usuários
└── manage.py          # Script de gerenciamento
```

Link do Projeto: [https://github.com/barscka/backend_pomodoro_task](https://github.com/barscka/backend_pomodoro_task)
```
