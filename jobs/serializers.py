from rest_framework import serializers
from django_typomatic import ts_interface
from .models import Tag, Job

@ts_interface()
class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Tag
        fields = ['id', 'name', 'color']
        read_only_fields = fields

@ts_interface()
class JobListSerializer(serializers.ModelSerializer):
    tags = TagSerializer(many=True, read_only=True)
    company = serializers.CharField(read_only=True)
    buttonLink = serializers.CharField(source='button_link', read_only=True)
    buttonText = serializers.CharField(source='button_text', read_only=True)

    class Meta:
        model  = Job
        fields = [
            'id', 'title',
            'company', 'company_image',
            'buttonLink', 'buttonText',
            'created_at', 'tags',
        ]
        read_only_fields = fields

@ts_interface()
class JobDetailSerializer(serializers.ModelSerializer):
    tags = TagSerializer(many=True, read_only=True)
    company = serializers.CharField(read_only=True)
    buttonLink = serializers.CharField(source='button_link', read_only=True)
    buttonText = serializers.CharField(source='button_text', read_only=True)

    class Meta:
        model  = Job
        fields = [
            'id', 'title',
            'description',
            'company', 'company_image', 'company_url', 'resume_url',
            'buttonLink', 'buttonText',
            'created_at', 'tags'
        ]
        read_only_fields = fields