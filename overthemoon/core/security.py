# core/security.py
from functools import wraps
from django.conf import settings
from django.http import Http404
from django.shortcuts import redirect

def shop_device_and_login_required(owner_only=False):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            # 1. Check if this device has the encrypted Shop Hardware Cookie
            device_token = request.get_signed_cookie('otm_shop_device', default=None, salt='zyrac_lock')
            if device_token != settings.SHOP_DEVICE_SECRET:
                # Return 404 so public scanners think the URL does not exist at all
                raise Http404("Page not found")

            # 2. Check Django Staff/Owner Login
            if not request.user.is_authenticated:
                return redirect('crm_login')

            # 3. Check Role (Owner = is_superuser, Kitchen Staff = is_staff)
            if owner_only and not request.user.is_superuser:
                return redirect('crm_orders_inbox')

            return view_func(request, *args, **kwargs)
        return _wrapped_view
    return decorator