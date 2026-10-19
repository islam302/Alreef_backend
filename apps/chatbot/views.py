from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.views import APIView

from apps.chatbot import services
from apps.chatbot.serializers import ChatReplySerializer, ChatRequestSerializer
from apps.core.response import success_response
from apps.core.schema import envelope


@extend_schema(tags=["Chat"], request=ChatRequestSerializer, responses=envelope(ChatReplySerializer, name="ChatReply"))
class ChatView(APIView):
    """
    POST /api/v1/chat/

    Anonymous and throttled (`chat` scope). Proxies the storefront's chat
    widget to the assistant so the provider's key stays on the server.
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_scope = "chat"

    def post(self, request):
        serializer = ChatRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        answer = services.ask(data["question"], data["history"], data["language"])
        return success_response({"answer": answer})
