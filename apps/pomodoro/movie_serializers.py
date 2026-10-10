from rest_framework import serializers


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        unknown = set(data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({key: 'Campo desconhecido.' for key in sorted(unknown)})
        return super().to_internal_value(data)


class DrawRequest(StrictSerializer):
    request_id = serializers.UUIDField()
    expected_state_version = serializers.IntegerField(min_value=0)


class DismissRequest(DrawRequest):
    pass


class AcceptRequest(DrawRequest):
    expected_progress_version = serializers.IntegerField(min_value=0)


class ProgressRequest(StrictSerializer):
    request_id = serializers.UUIDField()
    expected_version = serializers.IntegerField(min_value=0)
    status = serializers.ChoiceField(choices=['unwatched', 'watching', 'watched'])
    draw_id = serializers.UUIDField(required=False)


class CatalogQuery(StrictSerializer):
    collection_id = serializers.IntegerField(min_value=1, required=False)
    status = serializers.ChoiceField(choices=['unwatched', 'watching', 'watched'], required=False)
    search = serializers.CharField(required=False, allow_blank=True, max_length=200)
    page = serializers.IntegerField(min_value=1, required=False)
    page_size = serializers.IntegerField(min_value=1, max_value=100, required=False)


class TierMove(StrictSerializer):
    movie_id = serializers.IntegerField(min_value=1)
    tier = serializers.ChoiceField(choices=list('SABCD'), allow_null=True)


class TierRequest(StrictSerializer):
    request_id = serializers.UUIDField()
    expected_version = serializers.IntegerField(min_value=0)
    moves = TierMove(many=True, allow_empty=False, max_length=100)

    def validate_moves(self, moves):
        ids = [move['movie_id'] for move in moves]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError('Informe cada filme apenas uma vez.')
        return moves
