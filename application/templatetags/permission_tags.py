# application/templatetags/permission_tags.py
from django import template
from application.models import SpecialAccessUser

register = template.Library()

@register.filter
def can_access(user, permission_codename):
    from application.views import has_permission
    return has_permission(user, permission_codename)

@register.simple_tag
def get_user_permissions(user):
    """Get list of permission codenames user has"""
    permissions = set()
    
    if user.is_superuser:
        return ['all']
    
    # Group permissions
    for group in user.groups.all():
        for perm in group.permissions.all():
            permissions.add(perm.codename)
    
    # Special access permissions
    try:
        special_access = SpecialAccessUser.objects.get(user=user)
        # Handle cases where permission_type field doesn't exist
        if not hasattr(special_access, 'permission_type'):
            permissions.update(['view_dashboard', 'view_profile'])
        elif special_access.permission_type == 'all':
            permissions.add('all')
        else:
            # Add permissions based on special access type
            permission_map = {
                'franchise_management': ['view_franchise', 'add_franchise', 'change_franchise', 'view_dashboard', 'view_profile'],
                'fee_management': ['view_fee', 'process_payment', 'manage_fees', 'view_dashboard', 'view_profile'],
                'student_management': ['view_student', 'add_student', 'change_student', 'view_dashboard', 'view_profile'],
                'reporting': ['view_reports', 'view_dashboard', 'view_profile'],
            }
            permissions.update(permission_map.get(special_access.permission_type, []))
    except SpecialAccessUser.DoesNotExist:
        pass
    
    return list(permissions)