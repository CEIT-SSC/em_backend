from drf_spectacular.openapi import AutoSchema
from rest_framework import serializers, mixins
from functools import lru_cache

class BaseApiResponseSerializer(serializers.Serializer):
    success    = serializers.BooleanField(default=True, help_text="Indicates if the request was successful.")
    statusCode = serializers.IntegerField(default=200, help_text="The HTTP status code of the response.")
    message    = serializers.CharField(default="Request was successful.", help_text="A summary message for the response.")
    errors     = serializers.DictField(
        child=serializers.CharField(),
        default=dict,
        help_text="A dictionary of errors. Keys can be field names or 'detail'."
    )

_API_RESPONSE_CLASS_CACHE: dict[str, type[serializers.Serializer]] = {}

def get_api_response_serializer(data_serializer=None):
    if data_serializer is None:
        data_field = serializers.JSONField(read_only=True, allow_null=True, default=None)
        name = "ApiResponse_MessageOnly"
    else:
        if isinstance(data_serializer, serializers.BaseSerializer):
            data_field = data_serializer
            base_cls = data_serializer.__class__
        else:
            data_field = data_serializer()
            base_cls = data_serializer

        if isinstance(data_field, serializers.ListSerializer):
            child_name = data_field.child.__class__.__name__
            name = f"ApiResponse_{child_name}List"
        else:
            name = f"ApiResponse_{base_cls.__name__}"

    cached = _API_RESPONSE_CLASS_CACHE.get(name)
    if cached is not None:
        return cached

    DynamicApiResponseSerializer = type(
        name,
        (BaseApiResponseSerializer,),
        {
            "data": data_field,
            "Meta": type("Meta", (), {"ref_name": name}),
        },
    )

    _API_RESPONSE_CLASS_CACHE[name] = DynamicApiResponseSerializer
    return DynamicApiResponseSerializer


def get_paginated_response_serializer(item_serializer):
    item_name = (
        item_serializer.__name__
        if isinstance(item_serializer, type)
        else item_serializer.__class__.__name__
    )
    paginated_name = f"Paginated_{item_name}"

    PaginatedSerializer = type(
        paginated_name,
        (serializers.Serializer,),
        {
            "count": serializers.IntegerField(),
            "next": serializers.URLField(allow_null=True),
            "previous": serializers.URLField(allow_null=True),
            "results": (item_serializer(many=True) if isinstance(item_serializer, type) else
                        item_serializer.__class__(many=True)),
            "Meta": type("Meta", (), {"ref_name": paginated_name}),
        },
    )

    return get_api_response_serializer(PaginatedSerializer)


class NoPaginationAutoSchema(AutoSchema):
    def _get_paginator(self):
        return None

    def _is_list_view(self, *args, **kwargs):
        if getattr(self.view, 'action', None) == 'list':
            return False
        if isinstance(self.view, mixins.ListModelMixin) and self.method == 'GET':
            return False
        return super()._is_list_view(*args, **kwargs)

ApiErrorResponseSerializer = get_api_response_serializer(None)