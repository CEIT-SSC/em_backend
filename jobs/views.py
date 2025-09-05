from rest_framework import viewsets
from rest_framework.permissions import AllowAny
from rest_framework.filters import OrderingFilter
from drf_spectacular.utils import (
    extend_schema, extend_schema_view,
    OpenApiParameter, OpenApiTypes,
)
from django.db.models import Q
from em_backend.schemas import get_paginated_response_serializer, get_api_response_serializer, \
    ApiErrorResponseSerializer, NoPaginationAutoSchema
from .models import Job
from .serializers import JobListSerializer, JobDetailSerializer
from .pagination import JobPagination

list_params = [
    OpenApiParameter(
        name='tags',
        location=OpenApiParameter.QUERY,
        description='Comma-separated tag names **or** IDs. Example: `?tags=Remote,1,Full-Time`',
        type=OpenApiTypes.STR,
    ),
    OpenApiParameter(
        name='search',
        location=OpenApiParameter.QUERY,
        description='Keyword search in title, excerpt, description, and tag names.',
        type=OpenApiTypes.STR,
    ),
    OpenApiParameter(
        name='ordering',
        location=OpenApiParameter.QUERY,
        description='Sort results. `created_at`, `title`, or prepend **-** for descending.',
        type=OpenApiTypes.STR,
    ),
]

@extend_schema_view(
    list=extend_schema(
        tags=['Public - Jobs'],
        parameters=list_params,
        description='Paginated list of active jobs.',
        responses={
            200: get_paginated_response_serializer(JobListSerializer)
        }
    ),
    retrieve=extend_schema(
        tags=['Public - Jobs'],
        description='Job detail.',
        responses={
            200: get_api_response_serializer(JobDetailSerializer),
            404: ApiErrorResponseSerializer
        }
    ),
)
class JobViewSet(viewsets.ReadOnlyModelViewSet):
    schema = NoPaginationAutoSchema()
    permission_classes = [AllowAny]
    pagination_class = JobPagination
    serializer_class = JobListSerializer

    filter_backends   = [OrderingFilter]
    ordering_fields   = ['created_at', 'title']
    ordering          = ['-created_at']


    def get_queryset(self):
        qs = Job.objects.filter(is_active=True).prefetch_related('tags')
        params = self.request.query_params

        tags_param = params.get('tags')
        if tags_param:
            parts = [p.strip() for p in tags_param.split(',') if p.strip()]
            ids = [p for p in parts if p.isdigit()]
            names = [p for p in parts if not p.isdigit()]

            q_obj = Q()
            if ids:
                q_obj |= Q(tags__id__in=ids)
            if names:
                q_obj |= Q(tags__name__in=names)

            qs = qs.filter(q_obj).distinct()

        search = params.get('search')
        if search:
            qs = qs.filter(
                Q(title__icontains=search) |
                Q(description__icontains=search) |
                Q(excerpt__icontains=search) |
                Q(tags__name__icontains=search)
            ).distinct()

        return qs

    def get_serializer_class(self):
        return JobListSerializer if self.action == 'list' else JobDetailSerializer
