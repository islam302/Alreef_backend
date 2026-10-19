from django.urls import path

from apps.chatbot.views import ChatView

app_name = "chatbot"

urlpatterns = [
    path("", ChatView.as_view(), name="chat"),
]
