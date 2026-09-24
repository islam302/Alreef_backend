import uuid

from django.utils.deprecation import MiddlewareMixin

REQUEST_ID_HEADER = "X-Request-ID"


class RequestIDMiddleware(MiddlewareMixin):
    """Attaches a unique request id to every request/response for log correlation."""

    def process_request(self, request):
        request.request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())

    def process_response(self, request, response):
        request_id = getattr(request, "request_id", None)
        if request_id:
            response[REQUEST_ID_HEADER] = request_id
        return response
