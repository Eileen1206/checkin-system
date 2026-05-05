def user_permissions(request):
    is_admin_or_finance = (
        request.user.is_authenticated and (
            request.user.is_superuser or
            request.user.groups.filter(name__in=['admin', 'finance']).exists()
        )
    )

    pending_counts = {}
    if is_admin_or_finance:
        from .models import LeaveRequest, LocationCorrectionRequest
        pending_counts = {
            'leave_requests':        LeaveRequest.objects.filter(status='pending').count(),
            'location_corrections':  LocationCorrectionRequest.objects.filter(status='pending').count(),
        }
        pending_counts['total'] = sum(pending_counts.values())

    return {
        'is_admin_or_finance': is_admin_or_finance,
        'pending_counts': pending_counts,
    }