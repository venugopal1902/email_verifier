from django.shortcuts import render, redirect
from django.contrib.auth import logout
# from django.contrib.auth.decorators import login_required  <-- REMOVE THIS
from django.views.decorators.csrf import ensure_csrf_cookie

@ensure_csrf_cookie
def login_view(request):
    # REMOVED: The check that redirects to dashboard if already authenticated.
    # This allows the user to see the login form and get a new Token even if 
    # the server thinks they are already logged in.
    return render(request, 'login.html')

@ensure_csrf_cookie
def register_view(request):
    # REMOVED: The check that redirects to dashboard if already authenticated.
    return render(request, 'register.html')

# REMOVED: @login_required decorator
# This allows the dashboard HTML to load even if the user is a "Guest".
# The JavaScript inside dashboard.html will handle showing/hiding content.
def dashboard_view(request):
    return render(request, 'dashboard.html')

def logout_view(request):
    logout(request)
    return redirect('login')