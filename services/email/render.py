from django.template.loader import render_to_string


def render_email(*, html_template, text_template, context):
    html_body = render_to_string(html_template, context)
    text_body = render_to_string(text_template, context)
    return html_body, text_body
