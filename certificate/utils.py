from PIL import Image, ImageDraw, ImageFont
from django.contrib.staticfiles import finders
from io import BytesIO

def generate_certificate_image(cert_request=None):
    enrollment = cert_request.enrollment
    user = enrollment.user
    presentation = enrollment.presentation
    event = presentation.event

    name_text = f"{user.first_name} {user.last_name}"
    presentation_text = f"ارائه: {presentation.title}"
    event_text = f"رویداد: {event.title}"
    date_text = f"تاریخ: {cert_request.requested_at.date()}"

    background_path = finders.find("images/Certificate-Background.png")
    font_path = finders.find("fonts/Vazir.ttf")

    if not background_path:
        raise FileNotFoundError("Certificate background not found.")
    if not font_path:
        raise FileNotFoundError("Vazir font not found.")

    image = Image.open(background_path).convert("RGBA")
    draw = ImageDraw.Draw(image)

    # Smaller font sizes
    font_large = ImageFont.truetype(font_path, 36)
    font_small = ImageFont.truetype(font_path, 24)

    width, height = image.size

    def draw_rtl_centered(text, y, font):
        text_width = draw.textlength(text, font=font)
        x = (width - text_width) // 2
        draw.text((x, y), text, font=font, fill="black", direction="rtl")

    # Start y coordinate, then add vertical spacing
    start_y = int(height * 0.22)
    line_spacing = 50  # pixels between lines

    draw_rtl_centered(name_text, start_y, font_large)
    draw_rtl_centered(presentation_text, start_y + line_spacing, font_small)
    draw_rtl_centered(event_text, start_y + line_spacing * 2, font_small)
    draw_rtl_centered(date_text, start_y + line_spacing * 3, font_small)

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer
