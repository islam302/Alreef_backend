from rest_framework import serializers

# The storefront keeps the conversation and sends its last three exchanges.
# Anything longer is cut here too, so a hand-written request cannot make the
# upstream model read (and the shop pay for) an arbitrarily long transcript.
MAX_HISTORY = 6


class ChatTurnSerializer(serializers.Serializer):
    # No "system" role: that would let a visitor rewrite the assistant's brief.
    role = serializers.ChoiceField(choices=["user", "assistant"])
    content = serializers.CharField(max_length=4000, trim_whitespace=True)


class ChatRequestSerializer(serializers.Serializer):
    question = serializers.CharField(max_length=2000, trim_whitespace=True)
    history = ChatTurnSerializer(many=True, required=False, default=list)
    language = serializers.ChoiceField(choices=["ar", "en"], required=False, default="ar")

    def validate_history(self, value):
        return value[-MAX_HISTORY:]


class ChatReplySerializer(serializers.Serializer):
    answer = serializers.CharField()
