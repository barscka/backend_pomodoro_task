# Contrato temporal das atividades

## Convenção

- `starts_at`: instante absoluto do início da atividade, timezone-aware e transportado em UTC.
- `start_time` do `Schedule`: horário local sem data, usado somente para apresentação simples e regras locais.
- `expected_end_at`: instante absoluto previsto para conclusão, timezone-aware e transportado em UTC.
- `server_now`: instante absoluto do servidor no momento da resposta, timezone-aware e transportado em UTC.
- `completed_at` e `started_at`: instantes absolutos timezone-aware quando presentes.

Os cálculos do cronômetro usam `starts_at`, `expected_end_at` e `server_now`. A interface converte esses instantes para o timezone de apresentação e nunca extrai `HH:mm` diretamente da string ISO UTC.

O endpoint de histórico expõe `History.start_time` e `History.end_time`, que são `DateTimeField` e, portanto, também representam instantes absolutos. Eles devem ser convertidos para o timezone de apresentação antes da formatação.

## Exemplo

```json
{
  "starts_at": "2026-07-14T00:55:27.801146Z",
  "start_time": "21:55:27",
  "expected_end_at": "2026-07-14T01:20:27.801146Z",
  "server_now": "2026-07-14T00:58:30.035906Z"
}
```

Em `America/Sao_Paulo`, `starts_at` deve ser apresentado como `13/07/2026 21:55`. Não são necessários campos locais redundantes no payload porque o instante UTC já é inequívoco e o frontend possui timezone de apresentação configurado.
