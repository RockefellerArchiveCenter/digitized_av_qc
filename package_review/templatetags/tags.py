from django import template

register = template.Library()


@register.filter
def id_list(object_list):
    return ",".join([str(obj.pk) for obj in object_list])
