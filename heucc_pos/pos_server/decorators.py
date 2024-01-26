from django.http import HttpResponseForbidden

def local_network_only(view_func):
    def _wrapped_view(request, *args, **kwargs):
        allowed_ips = ['192.168.1.', '10.0.0.', '127.0.0.']  # Modify this as needed
        ip = request.META.get('REMOTE_ADDR')
        if not any(ip.startswith(allowed_ip) for allowed_ip in allowed_ips):
            return HttpResponseForbidden()
        return view_func(request, *args, **kwargs)
    return _wrapped_view
