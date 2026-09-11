import os
from django.db import models

class UploadedFile(models.Model):
    file = models.FileField(upload_to='file_server/uploads/', verbose_name="File")
    uploaded_at = models.DateTimeField(auto_now_add=True, verbose_name="Uploaded At")

    class Meta:
        verbose_name = "Uploaded File"
        verbose_name_plural = "Uploaded Files"
        ordering = ['-uploaded_at']

    def __str__(self):
        return os.path.basename(self.file.name)