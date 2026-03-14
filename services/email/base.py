class BaseEmailProvider:
    def send_email(self, to_email, subject, html_body, text_body=None):
        raise NotImplementedError
