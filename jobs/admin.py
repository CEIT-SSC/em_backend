from django.contrib import admin
from .models import Tag, Job

@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display   = ('name', 'color')
    search_fields  = ('name',)

@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display   = ('title', 'is_active', 'created_at',)
    list_filter    = ('is_active', 'tags')
    search_fields  = ('title', 'description')
    autocomplete_fields = ('tags',)
    readonly_fields    = ('created_at',)