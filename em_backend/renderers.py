import re
from rest_framework.renderers import JSONRenderer


class CustomJSONRenderer(JSONRenderer):
    charset = 'utf-8'
    DETAIL_KEYS = [
        "detail", "message", "error",
        "non_field_errors", "non_field_error"
    ]

    @staticmethod
    def _get_clean_error_message(error_value):
        text = str(error_value)
        while "ErrorDetail(string=" in text:
            match = re.search(r"string='([^']*)'", text)
            if match:
                text = match.group(1)
            else:
                break

        return text

    def _format_errors(self, data):
        if not isinstance(data, dict):
            return {"detail": self._get_clean_error_message(data)}

        formatted_errors = {}
        detail_messages = []
        message = None
        for key, value in data.items():
            clean_message = self._get_clean_error_message(value)
            if key in self.DETAIL_KEYS:
                detail_messages.append(clean_message)
                message = clean_message
            else:
                formatted_errors[key] = clean_message
                message = f"{key}: {clean_message}"

        if detail_messages:
            formatted_errors["detail"] = " ".join(detail_messages)

        return formatted_errors, message

    def render(self, data, accepted_media_type=None, renderer_context=None):
        response = renderer_context['response']
        status_code = response.status_code
        is_success = 200 <= status_code < 300

        response_data = {
            "success": is_success,
            "statusCode": status_code,
            "message": "",
            "errors": {},
            "data": None,
        }

        if is_success:
            response_data['data'] = data
            if isinstance(data, dict) and 'message' in data:
                response_data['message'] = data.pop('message')
                if not data:
                    response_data['data'] = None
            else:
                response_data['message'] = "Request was successful."
        else:
            response_data['message'] = "An error occurred."
            if data:
                errors, message = self._format_errors(data)
                response_data['errors'] = errors
                response_data['message'] = message

        return super().render(response_data, accepted_media_type, renderer_context)