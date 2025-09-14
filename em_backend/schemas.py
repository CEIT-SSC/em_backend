from drf_spectacular.openapi import AutoSchema
from rest_framework import serializers
from rest_framework import mixins


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
    if isinstance(item_serializer, serializers.BaseSerializer):
        item_cls = item_serializer.__class__
    else:
        item_cls = item_serializer
    item_name = getattr(item_cls, "__name__", item_cls.__class__.__name__)
    wrapper_name = f"Paginated_{item_name}"

    attrs = {
        'count': serializers.IntegerField(),
        'next': serializers.URLField(allow_null=True),
        'previous': serializers.URLField(allow_null=True),
        'results': item_cls(many=True),
        '__module__': __name__,
    }
    PaginatedSerializer = type(wrapper_name, (serializers.Serializer,), attrs)

    return get_api_response_serializer(PaginatedSerializer)


class EnvelopePaginationAutoSchema(AutoSchema):
    def _get_paginator(self):
        return None
    
    def _is_list_view(self, *args, **kwargs):
        if getattr(self.view, 'action', None) == 'list':
            return False
        if isinstance(self.view, mixins.ListModelMixin) and self.method == 'GET':
            return False
        return super()._is_list_view(*args, **kwargs)


ApiErrorResponseSerializer = get_api_response_serializer(None)