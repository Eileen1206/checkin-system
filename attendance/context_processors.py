def user_permissions(request):
    is_admin_or_finance = (
        request.user.is_authenticated and (
            request.user.is_superuser or
            request.user.groups.filter(name__in=['admin', 'finance']).exists()
        )
    )
    return {'is_admin_or_finance': is_admin_or_finance}