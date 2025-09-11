from drf_spectacular.openapi import AutoSchema
from rest_framework import serializers


class BaseApiResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField(default=True, help_text="Indicates if the request was successful.")
    statusCode = serializers.IntegerField(help_text="The HTTP status code of the response.")
    message = serializers.CharField(help_text="A summary message for the response.")
    errors = serializers.DictField(
        child=serializers.CharField(),
        default={},
        help_text="A dictionary of errors. Keys can be field names or 'detail'."
    )


def get_api_response_serializer(data_serializer=None):
    if data_serializer is None:
        data_field = serializers.ReadOnlyField(default={}, allow_null=True)
    else:
        if isinstance(data_serializer, serializers.BaseSerializer):
            data_field = data_serializer
        else:
            data_field = data_serializer()

    name = 'ApiResponse'
    if data_serializer:
        serializer_class = data_serializer if isinstance(data_serializer, type) else data_serializer.__class__
        if isinstance(data_serializer, serializers.ListSerializer):
            child_name = data_serializer.child.__class__.__name__
            name = f"ApiResponse_{child_name}List"
        else:
            name = f"ApiResponse_{serializer_class.__name__}"
    else:
        name = 'ApiResponse_MessageOnly'

    DynamicApiResponseSerializer = type(
        name,
        (BaseApiResponseSerializer,),
        {'data': data_field}
    )

    return DynamicApiResponseSerializer


def get_paginated_response_serializer(item_serializer):
    class PaginatedDataSerializer(serializers.Serializer):
        count = serializers.IntegerField()
        next = serializers.URLField(allow_null=True)
        previous = serializers.URLField(allow_null=True)
        results = item_serializer(many=True)

    return get_api_response_serializer(PaginatedDataSerializer)


class EnvelopePaginationAutoSchema(AutoSchema):
    def _get_paginator(self):
        return None


ApiErrorResponseSerializer = get_api_response_serializer(None)