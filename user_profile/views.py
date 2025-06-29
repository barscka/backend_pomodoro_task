from django.shortcuts import render
from .models import Profile

def home(request):
    try:
        profile = Profile.objects.first()
        context = {
            'profile': profile
        }
        return render(request, 'profile/home.html', context)
    except Profile.DoesNotExist:
        return render(request, 'profile/home.html', {})