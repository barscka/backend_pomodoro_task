import json

from django.conf import settings


class ApiDebugLoggingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not getattr(settings, 'API_DEBUG_LOG_ENABLED', False) or not request.path.startswith('/api/'):
            return self.get_response(request)

        request_payload = self._read_request_payload(request)
        try:
            response = self.get_response(request)
        except Exception as exc:
            self._emit_log(
                request=request,
                request_payload=request_payload,
                exception=exc,
            )
            raise

        self._emit_log(
            request=request,
            request_payload=request_payload,
            response=response,
        )
        return response

    def _emit_log(self, *, request, request_payload, response=None, exception=None):
        entry = {
            'type': 'api_debug',
            'method': request.method,
            'path': request.path,
            'query_params': dict(request.GET.items()),
            'request_json': request_payload,
        }

        if response is not None:
            entry['status_code'] = response.status_code
            entry['response_json'] = self._read_response_payload(response)

        if exception is not None:
            entry['exception'] = {
                'class': exception.__class__.__name__,
                'message': str(exception),
            }

        print(json.dumps(entry, ensure_ascii=False, default=str), flush=True)

    def _read_request_payload(self, request):
        body = request.body.strip()
        if not body:
            return None

        try:
            return json.loads(body.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._truncate_value(body.decode('utf-8', errors='replace'))

    def _read_response_payload(self, response):
        body = getattr(response, 'content', b'')
        if not body:
            return None

        try:
            return json.loads(body.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._truncate_value(body.decode('utf-8', errors='replace'))

    def _truncate_value(self, value):
        limit = 4000
        if len(value) <= limit:
            return value
        return f'{value[:limit]}...<truncated>'
