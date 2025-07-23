from PIL import Image, ImageDraw, ImageFont
from django.conf import settings
import os
from io import BytesIO

def generate_certificate_image(cert_request):
    enrollment = cert_request.enrollment
    user = enrollment.user
    presentation = enrollment.presentation

    background_path = os.path.join(settings.STATIC_ROOT, 'images', 'certificate_Background.png')
    font_path = os.path.join(settings.STATIC_ROOT, 'fonts', 'IRANSans.ttf')

    image = Image.open(background_path).convert("RGBA")
    draw = ImageDraw.Draw(image)

    name_font = ImageFont.truetype(font_path, size=60)
    text_font = ImageFont.truetype(font_path, size=40)

    # 🖋 Persian Text (only in the image)
    name_text = f"{user.first_name} {user.last_name}"
    presentation_text = f"شرکت در ارائه: {presentation.title}"
    date_text = f"تاریخ: {cert_request.requested_at.date()}"

    draw.text((500, 400), name_text, font=name_font, fill="black", anchor="mm")
    draw.text((500, 500), presentation_text, font=text_font, fill="black", anchor="mm")
    draw.text((500, 600), date_text, font=text_font, fill="black", anchor="mm")

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer
